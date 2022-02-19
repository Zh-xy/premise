"""
transformation.py contains the base class TransformationBase, used by other modules.
It provides basic methods usually used for electricity, cement, steel sectors transformation
on the wurst database.
"""

import csv
import uuid
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from itertools import product

import numpy as np
import xarray as xr
from wurst import geomatcher
from wurst import searching as ws
from wurst import transformations as wt

from premise.framework.logics import contains, does_not_contain, equals
from premise.transformation_tools import *

from . import DATA_DIR
from .activity_maps import InventorySet
from .geomap import Geomap, find_matching_regions
from .utils import c, create_scenario_label, get_fuel_properties, s


def get_suppliers_of_a_region(database, locations, names, reference_product, unit):
    """
    Return a list of datasets, for which the location, name, reference production and unit correspond
    to the region and name given, respectively.

    :param database: database to search
    :type database: list of dictionaries
    :param locations: list of locations
    :type locations: list
    :param names: names of datasets
    :type names: list
    :param unit: unit of dataset
    :type unit: str
    :param reference_product: reference product of dataset
    :type reference_product: str
    :return: list of wurst datasets
    :rtype: list
    """

    return ws.get_many(
        database,
        *[
            ws.either(*[ws.contains("name", supplier) for supplier in names]),
            ws.either(*[ws.equals("location", loc) for loc in locations]),
            ws.contains("reference product", reference_product),
            ws.equals("unit", unit),
        ],
    )


def get_shares_from_production_volume(ds_list):
    """
    Return shares of supply of each datasets in `ds_list` based on respective production volumes
    :param ds_list: list of datasets
    :type ds_list: list
    :return: dictionary with (dataset name, dataset location, ref prod, unit) as keys, shares as values. Shares total 1.
    :rtype: dict
    """

    if not isinstance(ds_list, list):
        ds_list = [ds_list]

    dict_act = {}
    total_production_volume = 0
    for act in ds_list:
        for exc in ws.production(act):
            # even if non-existent, we set a minimum value of 1e-9
            # because if not, we risk dividing by zero!!!
            production_volume = max(float(exc.get("production volume", 1e-9)), 1e-9)

            dict_act[
                (
                    act["name"],
                    act["location"],
                    act["reference product"],
                    act["unit"],
                )
            ] = production_volume
            total_production_volume += production_volume

    for dataset in dict_act:
        dict_act[dataset] /= total_production_volume

    return dict_act


def get_tuples_from_database(database):
    """
    Return a list of tuples (name, reference product, location)
    for each dataset in database.
    :param database:
    :return: a list of tuples
    :rtype: list
    """
    return [
        (dataset["name"], dataset["reference product"], dataset["location"])
        for dataset in database
    ]


class BaseTransformation:
    """
    Base transformation class.
    """

    def __init__(
        self,
        database: pd.DataFrame,
        iam_data: xr.DataArray,
        scenarios: dict,
    ):
        self.database = database
        self.iam_data = iam_data
        self.scenarios = scenarios
        self.scenario_labels = [
            create_scenario_label(
                scenario["model"], scenario["pathway"], scenario["year"]
            )
            for scenario in self.scenarios
        ]
        self.regions = {}
        for label in self.scenario_labels:
            self.regions[label] = [
                r
                for r in self.iam_data.electricity_markets.sel(
                    scenario=label
                ).region.values
                if self.iam_data.electricity_markets.sel(scenario=label, region=r).sum()
                > 0
            ]

        self.fuels_lhv = get_fuel_properties()
        # mapping = InventorySet(self.database)
        # self.emissions_map = mapping.get_remind_to_ecoinvent_emissions()
        # self.fuel_map = mapping.generate_fuel_map()
        self.fuels_co2 = get_fuel_properties()

        self.ecoinvent_to_iam_loc = {label: {} for label in self.scenario_labels}
        for s, scenario in enumerate(scenarios):
            geo = Geomap(model=scenario["model"])
            self.ecoinvent_to_iam_loc[self.scenario_labels[s]] = {
                loc: geo.ecoinvent_to_iam_location(loc)
                for loc in get_dataframe_locs(self.database)
            }

        self.iam_to_gains = {}
        for s, scenario in enumerate(scenarios):
            geo = Geomap(model=scenario["model"])
            self.iam_to_gains[self.scenario_labels[s]] = {
                loc: geo.iam_to_GAINS_region(loc)
                for loc in self.regions[self.scenario_labels[s]]
            }

        self.iam_to_ecoinvent_loc = {label: {} for label in self.scenario_labels}

        for s, scenario in enumerate(scenarios):
            geo = Geomap(model=scenario["model"])
            self.iam_to_ecoinvent_loc[self.scenario_labels[s]] = {
                loc: geo.iam_to_ecoinvent_location(loc)
                for loc in self.regions[self.scenario_labels[s]]
            }

        self.exchange_stack = []

        self.cache = {}

    def update_new_efficiency_in_comment(self, scenario, iam_loc, old_ei_eff, new_eff):
        """
        Update the old efficiency value in the ecoinvent dataset by the newly calculated one.
        :param dataset: dataset
        :type dataset: dict
        :param scaling_factor: scaling factor (new efficiency / old efficiency)
        :type scaling_factor: float
        """

        model, pathway, year = scenario.split("::")

        new_txt = (
            f" 'premise' has modified the efficiency of this dataset, from an original "
            f"{int(old_ei_eff * 100)}% to {int(new_eff * 100)}%, according to IAM model {model.upper()}, "
            f"scenario {pathway} for the region {iam_loc} in {year}."
        )

        return new_txt

    def get_iam_mapping(self, activity_map, technologies):
        """
        Define filter functions that decide which wurst datasets to modify.
        :param activity_map: a dictionary that contains 'technologies' as keys and activity names as values.
        :param fuels_map: a dictionary that contains 'technologies' as keys and fuel names as values.
        :param technologies: a list of IAM technologies.
        :return: dictionary that contains filters and functions
        :rtype: dict
        """

        return {
            tech: {
                "IAM_eff_func": self.find_iam_efficiency_change,
                "technology filters": activity_map[tech],
            }
            for tech in technologies
        }

    def fetch_proxies(self, name, ref_prod, production_variable, relink=False):
        """
        Fetch dataset proxies, given a dataset `name` and `reference product`.
        Store a copy for each IAM region.
        If a fitting ecoinvent location cannot be found for a given IAM region,
        fetch a dataset with a "RoW" location.
        Delete original datasets from the database.

        :param name: name of the datasets to find
        :type name: str
        :param ref_prod: reference product of the datasets to find
        :type ref_prod: str
        :param production_variable: name of variable in IAM data that refers to production volume
        :type production_variable: list or str
        :param relink: if `relink`, exchanges from the datasets will be relinked to
        the most geographically-appropriate providers from the database. This is computer-intensive.
        :type relink: bool
        :return:
        """

        d_map = {}

        for label in self.scenario_labels:
            _filter = (
                contains((s.exchange, c.cons_name), name)
                & contains((s.exchange, c.cons_prod), ref_prod)
                & equals((s.exchange, c.type), "production")
            )(self.database)

            d_map[label] = {
                self.ecoinvent_to_iam_loc[label][loc]: loc
                for loc in self.database[_filter]
                .loc[:, (s.exchange, c.cons_loc)]
                .unique()
            }

        # FIXME: doing so omits activity datasets that have a location
        # that can also be part of an IAM region
        # when there are multiple candidates, the last one is picked

        for label in self.scenario_labels:
            for region in set(self.regions[label]).difference(d_map[label].keys()):

                possible_locs = [
                    "RoW",
                    "GLO",
                ] + list(d_map[label].values())

                original = []
                count = 0

                while len(original) == 0:
                    _filter = (
                        contains((s.exchange, c.cons_name), name)
                        & contains((s.exchange, c.cons_prod), ref_prod)
                        & equals((s.exchange, c.type), "production")
                        & equals((s.exchange, c.cons_loc), possible_locs[count])
                    )(self.database)

                    count += 1

                    original = self.database[_filter]

                d_map[label][region] = original[(s.exchange, c.cons_loc)].values.item(0)

        d_act = defaultdict(dict)

        for scenario in self.scenario_labels:
            regions = (r for r in d_map[scenario] if r != "World")
            for region in regions:
                _filter = (
                    contains((s.exchange, c.cons_name), name)
                    & contains((s.exchange, c.cons_prod), ref_prod)
                    & equals((s.exchange, c.cons_loc), d_map[scenario][region])
                )(self.database)

                dataset = self.database[_filter].copy()

                d_act[scenario][region] = rename_location(
                    df=dataset, scenario=scenario, new_loc=region
                )

                # Add `production volume` field
                if production_variable in self.iam_data.production_volumes.variables:
                    prod_vol = (
                        self.iam_data.production_volumes.sel(
                            scenario=scenario,
                            region=region,
                            variables=production_variable,
                        )
                        .interp(year=int(scenario.split("::")[-1]))
                        .sum()
                        .values.item(0)
                    )
                else:
                    prod_vol = 0

                d_act[scenario][region] = change_production_volume(
                    d_act[scenario][region],
                    scenario,
                    prod_vol,
                )

                # relink technopshere exchanges
                if relink:
                    print(scenario, region, name)
                    d_act[scenario][region] = self.relink_technosphere_exchanges(
                        d_act[scenario][region], scenario
                    )

                # empty original dataset
                sel = _filter * ~equals((s.exchange, c.type), "production")(
                    self.database
                )
                self.database.loc[sel, (scenario, c.amount)] = 0

                # add a redirect exchange to the new dataset
                new_exc = empty_and_redirect_datasets(
                    dataset, scenario, region, original_loc=d_map[label][region]
                )
                # temporarily store the production volume in this new exchange
                new_exc[(scenario, c.cons_prod_vol)] = prod_vol

                self.exchange_stack.append(new_exc)

            # if a redirect exchange location links to more
            # than one IAM regional dataset
            # the amounts should be weighted based
            # on their respective production volume

            counts = Counter(d_map[scenario].values())
            repeated_locs = [loc for loc, count in counts.items() if count > 1]

            total_prod_vol = 0
            for loc in repeated_locs:
                for exc in self.exchange_stack:
                    if exc[(s.exchange, c.cons_loc)] == loc:
                        total_prod_vol += exc[(scenario, c.cons_prod_vol)]

            if total_prod_vol == 0:
                total_prod_vol = 1

            for loc in repeated_locs:
                for exc in self.exchange_stack:
                    if exc[(s.exchange, c.cons_loc)] == loc:
                        exc[(scenario, c.amount)] = (
                            exc[(scenario, c.cons_prod_vol)] / total_prod_vol
                        )
                        # delete the production volume value
                        exc[(scenario, c.cons_prod_vol)] = np.nan

        return d_act

    def relink_technosphere_exchanges(
        self,
        ds,
        scenario,
        exclusive=True,
        biggest_first=False,
        contained=True,
    ):

        __filters_tech = equals((s.exchange, c.type), "technosphere")(ds)

        new_exchanges = []

        for _, exc in ds[__filters_tech].iterrows():

            if exc[(s.exchange, c.cons_loc)] in self.cache:

                if (
                    exc[(s.exchange, c.prod_name)],
                    exc[(s.exchange, c.prod_prod)],
                    exc[(s.exchange, c.prod_loc)],
                    exc[(s.exchange, c.unit)],
                ) in self.cache[exc[(s.exchange, c.cons_loc)]]:

                    e = self.cache[exc[(s.exchange, c.cons_loc)]][
                        (
                            exc[(s.exchange, c.prod_name)],
                            exc[(s.exchange, c.prod_prod)],
                            exc[(s.exchange, c.prod_loc)],
                            exc[(s.exchange, c.unit)],
                        )
                    ]

                    new_exchanges.extend(
                        [
                            pd.Series(
                                [
                                    i[0],
                                    i[1],
                                    i[2],
                                    exc[(s.exchange, c.cons_name)],
                                    exc[(s.exchange, c.cons_prod)],
                                    exc[(s.exchange, c.cons_loc)],
                                    exc[(s.exchange, c.unit)],
                                    "technosphere",
                                    i[3],
                                    exc[(s.exchange, c.cons_key)],
                                    create_hash(i[3] + exc[(s.exchange, c.cons_key)]),
                                ]
                                + [
                                    exc[(s.ecoinvent, c.amount)] * i[-1],
                                    exc[(s.ecoinvent, c.efficiency)],
                                    exc[(s.ecoinvent, c.comment)],
                                    exc[(s.ecoinvent, c.cons_prod_vol)],
                                ]
                                * (len(self.scenario_labels) + 1)
                            )
                            for i in e
                        ]
                    )

                    continue

            __filter = (
                equals((s.exchange, c.type), "production")
                & equals((s.exchange, c.cons_name), exc[(s.exchange, c.prod_name)])
                & equals((s.exchange, c.cons_prod), exc[(s.exchange, c.prod_prod)])
            )(self.database)

            possible_datasets = self.database[__filter]

            possible_locations = get_dataframe_locs(possible_datasets)

            if exc[(s.exchange, c.cons_loc)] in possible_locations:

                exc[(s.exchange, c.prod_loc)] = exc[(s.exchange, c.cons_loc)]
                new_exchanges.append(exc)
                continue

            if len(possible_datasets) > 0:

                eligible_suppliers = find_matching_regions(
                    exc,
                    possible_locations,
                    contained,
                    exclusive,
                    biggest_first,
                    possible_datasets,
                    model=scenario.split("::")[0],
                    iam_regions=self.regions[scenario],
                )

                if not eligible_suppliers:
                    new_exchanges.append(exc)
                    continue

                allocated, share = self.allocate_inputs(exc, eligible_suppliers)
                new_exchanges.extend(allocated)
                self.write_cache(exc, allocated, share)

            else:

                new_exchanges.append(exc)

                # add to cache
                self.write_cache(exc, [exc], [1])

        __filters_tech = does_not_contain((s.exchange, c.type), "technosphere")(ds)
        ds = ds[__filters_tech]

        if len(new_exchanges) > 0:
            ds = pd.concat([ds, pd.concat(new_exchanges, axis=1).T])

        return ds

    def write_cache(self, exc, allocated, share):

        if exc[(s.exchange, c.cons_loc)] not in self.cache:
            self.cache[exc[(s.exchange, c.cons_loc)]] = {}

        self.cache[exc[(s.exchange, c.cons_loc)]][
            (
                exc[(s.exchange, c.prod_name)],
                exc[(s.exchange, c.prod_prod)],
                exc[(s.exchange, c.prod_loc)],
                exc[(s.exchange, c.prod_key)],
            )
        ] = [
            (
                e[(s.exchange, c.cons_name)],
                e[(s.exchange, c.cons_prod)],
                e[(s.exchange, c.cons_loc)],
                e[(s.exchange, c.cons_key)],
                _s,
            )
            for e, _s in zip(allocated, share)
        ]

    def allocate_inputs(self, exc, lst):
        """Allocate the input exchanges in ``lst`` to ``exc``,
        using production volumes where possible, and equal splitting otherwise.
        Always uses equal splitting if ``RoW`` is present."""
        has_row = any((x[(s.exchange, c.cons_loc)] in ("RoW", "GLO") for x in lst))
        pvs = [i[(s.ecoinvent, c.cons_prod_vol)] or 0 for i in lst]
        if all((x > 0 for x in pvs)) and not has_row:
            # Allocate using production volume
            total = sum(pvs)
        else:
            # Allocate evenly
            total = len(lst)
            pvs = [1 for _ in range(total)]

        def new_exchange(exc, location, factor):
            cp = deepcopy(exc)
            cp[(s.exchange, c.cons_loc)] = location
            cols = [col for col in cp.index if col[1] == c.amount]
            cp[cols] = factor * cp[(s.ecoinvent, c.amount)]
            return cp

        return [
            new_exchange(exc, obj[(s.exchange, c.cons_loc)], factor / total)
            for obj, factor in zip(lst, pvs)
        ], [p / total for p in pvs]

    def relink_datasets(self, excludes_datasets, alternative_names=None):
        """
        For a given exchange name, product and unit, change its location to an IAM location,
        to effectively link to the newly buil_t market(s)/activity(ies).

        :param name: dataset name
        :type name: str
        :param ref_product: reference product of the dataset
        :type ref_product: str
        :param unit: unit of the dataset
        :type unit: str
        :param does_not_contain: list of terms that, if contained in the name of an exchange, should be ignored
        :type does_not_contain: list
        :returns: does not return anything. Modifies in place.
        """

        # loop through the database
        # ignore datasets which name contains `name`
        for act in ws.get_many(
            self.database,
            ws.doesnt_contain_any("name", excludes_datasets),
        ):
            # and find exchanges of datasets to relink

            excs_to_relink = (
                exc
                for exc in act["exchanges"]
                if exc["type"] == "technosphere"
                and (exc["name"], exc["product"], exc["location"])
                not in self.list_datasets
            )

            unique_excs_to_relink = list(
                set(
                    (exc["name"], exc["product"], exc["unit"]) for exc in excs_to_relink
                )
            )

            for exc in unique_excs_to_relink:

                alternative_names = [exc[0], *alternative_names]
                alternative_locations = (
                    [act["location"]]
                    if act["location"] in self.regions
                    else [self.ecoinvent_to_iam_loc[act["location"]]]
                )

                for alt_name, alt_loc in product(
                    alternative_names, alternative_locations
                ):

                    if (alt_name, exc[1], alt_loc) in self.list_datasets:
                        # print(f"found! {alt_name, alt_loc}")
                        break

                # summing up the amounts provided by the unwanted exchanges
                # and remove these unwanted exchanges from the dataset
                amount = sum(
                    e["amount"]
                    for e in excs_to_relink
                    if (e["name"], e["product"]) == exc
                )
                act["exchanges"] = [
                    e for e in act["exchanges"] if (e["name"], e.get("product")) != exc
                ]

                # create a new exchange, with the new provider
                try:
                    new_exc = {
                        "name": alt_name,
                        "product": exc[1],
                        "amount": amount,
                        "type": "technosphere",
                        "unit": exc[2],
                        "location": alt_loc,
                    }

                    act["exchanges"].append(new_exc)

                except:
                    print(
                        f"No alternative provider found for {exc[0], act['location']}."
                    )

    def get_carbon_capture_rate(self, loc, sector):
        """
        Returns the carbon capture rate as indicated by the IAM
        It is calculated as CO2 captured / (CO2 captured + CO2 emitted)

        :param loc: location of the dataset
        :return: rate of carbon capture
        :param sector: name of the sector to look capture rate for
        :type sector: str or list

        :rtype: float
        """

        if sector in self.iam_data.carbon_capture_rate.variables.values:
            rate = self.iam_data.carbon_capture_rate.sel(
                variables=sector,
                region=loc,
            ).values
        else:
            rate = 0

        return rate

    def find_gains_emissions_change(self, pollutant, location, sector):
        """
        Return the relative change in emissions for a given pollutant, location and sector.
        :param pollutant: name of pollutant
        :param sector: name of technology/sector
        :param location: location of emitting dataset
        :return: a scaling factor
        :rtype: float
        """

        scaling_factor = self.iam_data.emissions.loc[
            dict(
                region=location,
                pollutant=pollutant,
                sector=sector,
            )
        ].values.item(0)

        return scaling_factor

    def find_iam_efficiency_change(self, variable, location, year, scenario):
        """
        Return the relative change in efficiency for `variable` in `location`
        relative to 2020.
        :param variable: IAM variable name
        :param location: IAM region
        :return: relative efficiency change (e.g., 1.05)
        :rtype: float
        """

        scaling_factor = self.iam_data.efficiency.sel(
            region=location, variables=variable, year=int(year), scenario=scenario
        ).values.item(0)

        if scaling_factor in (np.nan, np.inf):
            scaling_factor = 1

        return scaling_factor
