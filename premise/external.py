"""
Implements external scenario data.
"""

import csv
from datetime import date

import xarray as xr
import yaml
from numpy import ndarray
from wurst import searching as ws

from . import DATA_DIR
from .clean_datasets import get_biosphere_flow_uuid
from .transformation import *
from .utils import eidb_label


def flag_activities_to_adjust(
    dataset: dict, scenario_data: dict, year: int, dataset_vars: dict
) -> dict:
    """
    Flag datasets that will need to be adjusted.
    :param dataset: dataset to be adjusted
    :param scenario_data: external scenario data
    :param year: year of the external scenario
    :param dataset_vars: variables of the dataset
    :return: dataset with additional info on variables to adjust
    """

    regions = scenario_data["production volume"].region.values.tolist()
    if "except regions" in dataset_vars:
        regions = [r for r in regions if r not in dataset_vars["except regions"]]

    dataset["regions"] = regions

    # add potential technosphere or biosphere filters
    if "efficiency" in dataset_vars:
        dataset["adjust efficiency"] = True

        d_tech_filters = {
            k.get("variable"): [
                k.get("includes").get("technosphere"),
                {
                    region: find_iam_efficiency_change(
                        k["variable"],
                        region,
                        scenario_data["efficiency"],
                        year,
                    )
                    for region in regions
                },
            ]
            for k in dataset_vars["efficiency"]
            if "technosphere" in k.get("includes", {})
        }

        d_tech_filters.update(
            {
                k.get("variable"): [
                    None,
                    {
                        region: find_iam_efficiency_change(
                            k["variable"],
                            region,
                            scenario_data["efficiency"],
                            year,
                        )
                        for region in regions
                    },
                ]
                for k in dataset_vars["efficiency"]
                if "includes" not in k
            }
        )

        d_bio_filters = {
            k.get("variable"): [
                k.get("includes").get("biosphere"),
                {
                    region: find_iam_efficiency_change(
                        k["variable"],
                        region,
                        scenario_data["efficiency"],
                        year,
                    )
                    for region in regions
                },
            ]
            for k in dataset_vars["efficiency"]
            if "biosphere" in k.get("includes", {})
        }

        d_bio_filters.update(
            {
                k.get("variable"): [
                    None,
                    {
                        region: find_iam_efficiency_change(
                            k["variable"],
                            region,
                            scenario_data["efficiency"],
                            year,
                        )
                        for region in regions
                    },
                ]
                for k in dataset_vars["efficiency"]
                if "includes" not in k
            }
        )

        if d_tech_filters:
            dataset["technosphere filters"] = d_tech_filters

        if d_bio_filters:
            dataset["biosphere filters"] = d_bio_filters

    if dataset_vars["replaces"]:
        dataset["replaces"] = dataset_vars["replaces"]

    if dataset_vars["replaces in"]:
        dataset["replaces in"] = dataset_vars["replaces in"]

    if dataset_vars["replacement ratio"] != 1.0:
        dataset["replacement ratio"] = dataset_vars["replacement ratio"]

    if dataset_vars["regionalize"]:
        dataset["regionalize"] = dataset_vars["regionalize"]

    return dataset


def find_iam_efficiency_change(
    variable: Union[str, list], location: str, efficiency_data, year: int
) -> float:
    """
    Return the relative change in efficiency for `variable` in `location`
    relative to 2020.
    :param variable: IAM variable name
    :param location: IAM region
    :return: relative efficiency change (e.g., 1.05)
    """

    scaling_factor = 1

    if variable in efficiency_data.variables.values:

        scaling_factor = (
            efficiency_data.sel(region=location, variables=variable).interp(year=year)
        ).values.item(0)

        if scaling_factor in (np.nan, np.inf):
            scaling_factor = 1

    return scaling_factor


def get_recursively(search_dict: dict, field: str) -> list:
    """Takes a dict with nested lists and dicts,
    and searches all dicts for a key of the field
    provided.
    :param search_dict: dict with nested lists and dicts
    :param field: field to search for
    :return: list of values for the field
    """
    fields_found = []

    for key, value in search_dict.items():

        if key == field:
            fields_found.append(value)

        elif isinstance(value, dict):
            results = get_recursively(value, field)
            for result in results:
                fields_found.append(result)

        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    more_results = get_recursively(item, field)
                    for another_result in more_results:
                        fields_found.append(another_result)

    return fields_found


def adjust_efficiency(dataset: dict) -> dict:
    """
    Adjust the input-to-output efficiency of a dataset and return it back.
    :param dataset: dataset to be adjusted
    :return: adjusted dataset
    """

    # loop through the type of flows to adjust
    for eff_type in ["technosphere", "biosphere"]:

        if f"{eff_type} filters" in dataset:
            for k, v in dataset[f"{eff_type} filters"].items():

                # the scaling factor is the inverse of the efficiency change
                if len(dataset["regions"]) > 1:
                    try:
                        scaling_factor = 1 / v[1][dataset["location"]]
                    except KeyError as err:
                        print(dataset["name"], dataset["location"], dataset["regions"])
                        raise KeyError(
                            f"No efficiency factor provided for region {dataset['location']}"
                        ) from err
                else:
                    scaling_factor = 1 / v[1].get(dataset["regions"][0], 1)
                filters = v[0]

                if eff_type == "technosphere":

                    # adjust technosphere flows
                    # all of them if no filters are provided

                    if filters:
                        for exc in ws.technosphere(
                            dataset,
                            ws.either(*[ws.contains("name", x) for x in filters]),
                        ):
                            wurst.rescale_exchange(exc, scaling_factor)
                    else:
                        for exc in ws.technosphere(
                            dataset,
                        ):
                            wurst.rescale_exchange(exc, scaling_factor)

                else:

                    # adjust biosphere flows
                    # all of them if a filter is not provided

                    if filters:
                        for exc in ws.biosphere(
                            dataset,
                            ws.either(*[ws.contains("name", x) for x in filters]),
                        ):
                            wurst.rescale_exchange(exc, scaling_factor)
                    else:
                        for exc in ws.biosphere(
                            dataset,
                        ):
                            wurst.rescale_exchange(exc, scaling_factor)
    return dataset


def fetch_dataset_description_from_production_pathways(
    config: dict, item: str
) -> tuple[str, str, bool, bool, bool]:
    """
    Fetch a few ecoinvent variables for a given production pathway
    in the config file, such as the name, reference product, etc.
    :param config: config file
    :param item: production pathway
    :return: dictionary with variables
    """
    for p, v in config["production pathways"].items():
        if p == item:
            if "exists in original database" not in v["ecoinvent alias"]:
                v["ecoinvent alias"].update({"exists in original database": True})

            if "new dataset" not in v["ecoinvent alias"]:
                v["ecoinvent alias"].update({"new dataset": False})

            if "regionalize" not in v["ecoinvent alias"]:
                v["ecoinvent alias"].update({"regionalize": False})

            return (
                v["ecoinvent alias"]["name"],
                v["ecoinvent alias"]["reference product"],
                v["ecoinvent alias"]["exists in original database"],
                v["ecoinvent alias"]["new dataset"],
                v["ecoinvent alias"]["regionalize"],
            )


def fetch_var(config_file: dict, list_vars: list) -> list:
    """
    Return the value for `variable` for the given list of variables.
    :param config_file: config file
    :param list_vars: list of variables
    :return: value for `variable`
    """

    return [
        config_file["production pathways"][v]["production volume"]["variable"]
        for v in list_vars
    ]


class ExternalScenario(BaseTransformation):
    def __init__(
        self,
        database: List[dict],
        iam_data: IAMDataCollection,
        external_scenarios: list,
        external_scenarios_data: dict,
        model: str,
        pathway: str,
        year: int,
    ):
        """
        :param database: list of datasets representing teh database
        :param iam_data: IAM data: production volumes, efficiency, etc.
        :param external_scenarios: list of data packages representing the external scenarios
        :param external_scenarios_data: IAM data: production volumes, efficiency, etc.
        :param model: model name
        :param pathway: pathway name
        :param year: year

        """
        super().__init__(database, iam_data, model, pathway, year)
        self.datapackages = external_scenarios
        self.external_scenarios_data = external_scenarios_data

        for i, datapackage in enumerate(self.datapackages):
            external_scenario_regions = self.external_scenarios_data[i]["regions"]
            # Open corresponding config file
            resource = datapackage.get_resource("config")
            config_file = yaml.safe_load(resource.raw_read())
            ds_names = get_recursively(config_file, "name")
            self.regionalize_inventories(ds_names, external_scenario_regions)
        self.dict_bio_flows = get_biosphere_flow_uuid()

    def regionalize_inventories(self, ds_names, regions) -> None:
        """
        Produce IAM region-specific version of the dataset.
        :param regions: list of regions to produce datasets for

        """

        for ds in ws.get_many(
            self.database,
            ws.equals("regionalize", True),
            ws.either(*[ws.contains("name", name) for name in ds_names]),
        ):

            # Check if datasets already exist for IAM regions
            # if not, create them
            if ds["location"] not in regions:
                new_acts = self.fetch_proxies(
                    name=ds["name"],
                    ref_prod=ds["reference product"],
                    regions=ds["regions"],
                )

                # add new datasets to database
                self.database.extend(new_acts.values())

            # remove "adjust efficiency" tag
            del ds["regionalize"]

        # some datasets might be meant to replace the supply
        # of other datasets, so we need to adjust those
        replacing_acts = list(
            (
                {
                    "replaces": ds["replaces"],
                    "replaces in": ds.get("replaces in", None),
                    "name": ds["name"],
                    "reference product": ds["reference product"],
                    "replacement ratio": ds.get("replacement ratio", 1),
                    "regions": ds.get("regions", regions),
                }
                for ds in self.database
                if "replaces" in ds and ds["name"] in ds_names
            )
        )

        for ds in replacing_acts:
            self.relink_to_new_datasets(
                replaces=ds["replaces"],
                replaces_in=ds.get("replaces in", None),
                new_name=ds["name"],
                new_ref=ds["reference product"],
                ratio=ds.get("replacement ratio", 1),
                regions=ds.get("regions", regions),
            )

        # adjust efficiency of datasets
        for dataset in ws.get_many(
            self.database,
            ws.equals("adjust efficiency", True),
            ws.either(*[ws.contains("name", name) for name in ds_names]),
        ):
            adjust_efficiency(dataset)
            del dataset["adjust efficiency"]

    def get_market_dictionary_structure(self, market: dict, region: str) -> dict:
        """
        Return a dictionary for market creation, given the location passed.
        To be further filled with exchanges.
        :param market: dataset to use as template
        :param region: region to create the dataset for.
        :return: dictionary
        """

        return {
            "name": market["name"],
            "reference product": market["reference product"],
            "unit": market["unit"],
            "location": region,
            "database": eidb_label(self.model, self.scenario, self.year),
            "code": str(uuid.uuid4().hex),
            "exchanges": [
                {
                    "name": market["name"],
                    "product": market["reference product"],
                    "unit": market["unit"],
                    "location": region,
                    "type": "production",
                    "amount": 1,
                }
            ],
        }

    def fill_in_world_market(
        self, market: dict, regions: list, i: int, pathways: list
    ) -> dict:
        """
        Fill in the world market with the supply of all regional markets
        based on their respective production volumes.
        :param market: World market dataset
        :param regions: List of regions
        :param i: index of production volumes array in external_data
        :param pathways: List of production pathways
        :return: World market dataset
        """

        # fetch a template for the world market dataset
        world_market = self.get_market_dictionary_structure(market, "World")
        new_excs = []

        # fetch the supply share for each regional market
        for region in regions:
            supply_share = np.clip(
                (
                    self.external_scenarios_data[i]["production volume"]
                    .sel(region=region, year=self.year, variables=pathways)
                    .sum(dim="variables")
                    / self.external_scenarios_data[i]["production volume"]
                    .sel(year=self.year, variables=pathways)
                    .sum(dim=["variables", "region"])
                ).values.item(0),
                0,
                1,
            )

            # create a new exchange for the regional market
            # in the World market dataset
            new_excs.append(
                {
                    "name": market["name"],
                    "product": market["reference product"],
                    "unit": market["unit"],
                    "location": region,
                    "type": "technosphere",
                    "amount": supply_share,
                }
            )

        world_market["exchanges"].extend(new_excs)

        return world_market

    def check_existence_of_market_suppliers(self):
        """
        Check if the market suppliers are in the database.

        """

        # Loop through custom scenarios
        for i, dp in enumerate(self.datapackages):

            # Open corresponding config file
            resource = dp.get_resource("config")
            config_file = yaml.safe_load(resource.raw_read())

            # Check if information on market creation is provided
            if "markets" in config_file:

                for market in config_file["markets"]:

                    # Loop through the technologies that should compose the market
                    for pathway_to_include in market["includes"]:

                        # fetch the dataset name/ref corresponding to this item
                        # under `production pathways`
                        (
                            name,
                            ref_prod,
                            exists_in_database,
                            new_dataset,
                            regionalize_dataset,
                        ) = fetch_dataset_description_from_production_pathways(
                            config_file, pathway_to_include
                        )

                        if not new_dataset:
                            regions = self.external_scenarios_data[i]["regions"]

                            # try to see if we find a provider for these regions
                            if any(region in self.regions for region in regions):
                                ecoinvent_regions = [
                                    self.geo.iam_to_ecoinvent_location(region)
                                    for region in regions
                                ]
                            else:
                                ecoinvent_regions = [
                                    r
                                    if isinstance(r, str) or r[0] == "ecoinvent"
                                    else None
                                    for r in [
                                        y
                                        for x in regions
                                        for y in self.geo.geo.within(x)
                                    ]
                                ]

                                ecoinvent_regions = [
                                    i for i in ecoinvent_regions if i and i != "GLO"
                                ]

                                if len(ecoinvent_regions) == 0:
                                    ecoinvent_regions = [
                                        i
                                        for i in list(self.geo.geo.keys())
                                        if isinstance(i, str) and i != "GLO"
                                    ]

                            possible_locations = [
                                *regions,
                                *ecoinvent_regions,
                                "RER",
                                "Europe without Switzerland",
                                "RoW",
                                "GLO",
                            ]

                            suppliers, counter = [], 0

                            # we loop through the possible locations
                            # by order of preference
                            try:
                                while not suppliers:
                                    suppliers = list(
                                        ws.get_many(
                                            self.database,
                                            ws.equals("name", name),
                                            ws.equals(
                                                "reference product",
                                                ref_prod,
                                            ),
                                            ws.equals(
                                                "location", possible_locations[counter]
                                            ),
                                        )
                                    )

                                    counter += 1

                            except IndexError:
                                raise ValueError(
                                    f"Regionalized datasets for pathway {pathway_to_include} "
                                    f"with `name` {name} and `reference product` {ref_prod} "
                                    f"cannot be found in "
                                    f"locations {possible_locations}."
                                )

                            if not exists_in_database or regionalize_dataset:
                                for ds in suppliers:
                                    ds["custom scenario dataset"] = True

    def fetch_supply_share(self, i: int, region: str, var: str, vars: list) -> ndarray:
        """
        Return the supply share of a given variable in a given region.
        :param i: index of the scenario
        :param region: region
        :param var: variable
        :param vars: list of all variables
        :return: ndarray
        """
        return np.clip(
            (
                self.external_scenarios_data[i]["production volume"]
                .sel(
                    region=region,
                    variables=var,
                )
                .interp(year=self.year)
                / self.external_scenarios_data[i]["production volume"]
                .sel(
                    region=region,
                    variables=vars,
                )
                .interp(year=self.year)
                .sum(dim="variables")
            ).values.item(0),
            0,
            1,
        )

    def fetch_potential_suppliers(
        self, possible_locations: list, name: str, ref_prod: str
    ) -> list:
        """
        Fetch the potential suppliers for a given name and reference product.
        :param possible_locations: list of possible locations
        :param name: name of the dataset
        :param ref_prod: reference product of the dataset
        :return: list of potential suppliers
        """

        act, counter = [], 0
        try:
            while not act:
                act = list(
                    ws.get_many(
                        self.database,
                        ws.equals("name", name),
                        ws.equals(
                            "reference product",
                            ref_prod,
                        ),
                        ws.equals("location", possible_locations[counter]),
                    )
                )

                counter += 1
        except IndexError:
            print("Cannot find -> ", name, ref_prod, possible_locations)

        return act

    def write_suppliers_exchanges(self, suppliers: dict, supply_share: float) -> list:
        """
        Write the exchanges for the suppliers.
        :param suppliers: list of suppliers
        :param supply_share: supply share
        :return: list of exchanges
        """

        new_excs = []

        for supplier, market_share in suppliers.items():
            provider_share = supply_share * market_share

            new_excs.append(
                {
                    "name": supplier[0],
                    "product": supplier[2],
                    "unit": supplier[-1],
                    "location": supplier[1],
                    "type": "technosphere",
                    "amount": provider_share,
                    "uncertainty type": 0,
                }
            )

        return new_excs

    def add_additional_exchanges(self, additional_exc: dict, region: str) -> list:
        """
        Add additional exchanges to a dataset.
        """

        name = additional_exc["name"]
        ref_prod = additional_exc.get("reference product")
        categories = additional_exc.get("categories")
        unit = additional_exc.get("unit")
        amount = additional_exc["amount"]

        if ref_prod:
            # this is a technosphere exchange
            ecoinvent_regions = [
                r[-1]
                for r in self.geo.geo.within(region)
                if r[0] == "ecoinvent" and r[-1] not in ["GLO", "RoW"]
            ]

            possible_locations = [
                region,
                *ecoinvent_regions,
                "RER",
                "Europe without Switzerland",
                "RoW",
                "GLO",
            ]
            potential_suppliers = self.fetch_potential_suppliers(
                possible_locations, name, ref_prod
            )
            suppliers = get_shares_from_production_volume(potential_suppliers)

            return self.write_suppliers_exchanges(suppliers, amount)

        else:
            # this is a biosphere exchange
            categories = tuple(categories.split("::"))
            if len(categories) == 1:
                categories += ("unspecified",)

            key = (name, categories[0], categories[1], unit)

            return [
                {
                    "name": name,
                    "unit": unit,
                    "categories": categories,
                    "type": "biosphere",
                    "amount": amount,
                    "uncertainty type": 0,
                    "input": (
                        "biosphere3",
                        self.dict_bio_flows[key],
                    ),
                }
            ]

    def adjust_efficiency_of_new_markets(
        self, datatset: dict, vars: dict, region: str, eff_data: xr.DataArray
    ) -> dict:

        for ineff in vars["efficiency"]:
            scaling_factor = 1 / find_iam_efficiency_change(
                ineff["variable"], region, eff_data, self.year
            )

            if not "includes" in ineff:

                wurst.change_exchanges_by_constant_factor(datatset, scaling_factor)

            else:

                if "technosphere" in ineff["includes"]:
                    fltr = []
                    for y in ineff["includes"]["technosphere"]:
                        for k, v in y.items():
                            fltr.append(wurst.contains(k, v))

                    for exc in ws.technosphere(datatset, *(fltr or [])):
                        wurst.rescale_exchange(exc, scaling_factor)

                if "biosphere" in ineff["includes"]:
                    fltr = []
                    for y in ineff["includes"]["biosphere"]:
                        for k, v in y.items():
                            fltr.append(wurst.contains(k, v))

                    for exc in ws.biosphere(datatset, *(fltr or [])):
                        wurst.rescale_exchange(exc, scaling_factor)
        return datatset

    def create_custom_markets(self) -> None:
        """
        Create new custom markets, and create a `World` market
        if no data is provided for it.

        """

        self.check_existence_of_market_suppliers()

        # Loop through custom scenarios
        for i, dp in enumerate(self.datapackages):

            # Open corresponding config file
            resource = dp.get_resource("config")
            config_file = yaml.safe_load(resource.raw_read())

            # Check if information on market creation is provided
            if "markets" in config_file:
                print("Create custom markets.")

                for market_vars in config_file["markets"]:

                    # fetch all scenario file vars that
                    # relate to this market

                    pathways = market_vars["includes"]

                    vars = fetch_var(config_file, pathways)

                    # Check if there are regions we should not
                    # create a market for
                    regions = self.external_scenarios_data[i]["regions"]

                    if "except regions" in market_vars:
                        regions = [
                            r for r in regions if r not in market_vars["except regions"]
                        ]

                    # Loop through regions
                    for region in regions:

                        # Create market dictionary
                        new_market = self.get_market_dictionary_structure(
                            market_vars, region
                        )

                        new_excs = []
                        for pathway in pathways:

                            var = fetch_var(config_file, [pathway])[0]

                            # fetch the dataset name/ref corresponding to this item
                            # under `production pathways`
                            (
                                name,
                                ref_prod,
                                exists_in_database,
                                new_dataset,
                                _,
                            ) = fetch_dataset_description_from_production_pathways(
                                config_file, pathway
                            )

                            # try to see if we find a provider with that region
                            if region in self.regions:
                                ecoinvent_regions = []

                            else:
                                ecoinvent_regions = [
                                    r
                                    if isinstance(r, str) or r[0] == "ecoinvent"
                                    else None
                                    for r in [
                                        y
                                        for x in regions
                                        for y in self.geo.geo.within(x)
                                    ]
                                ]

                                ecoinvent_regions = [
                                    i for i in ecoinvent_regions if i and i != "GLO"
                                ]

                                if len(ecoinvent_regions) == 0:
                                    ecoinvent_regions = [
                                        i
                                        for i in list(self.geo.geo.keys())
                                        if isinstance(i, str) and i != "GLO"
                                    ]

                            possible_locations = [
                                region,
                                *ecoinvent_regions,
                                "RER",
                                "Europe without Switzerland",
                                "RoW",
                                "GLO",
                            ]

                            potential_suppliers = self.fetch_potential_suppliers(
                                possible_locations, name, ref_prod
                            )

                            # supply share = production volume of that technology in this region
                            # over production volume of all technologies in this region

                            try:
                                supply_share = self.fetch_supply_share(
                                    i, region, var, vars
                                )

                            except KeyError:
                                print(
                                    "suppliers for ",
                                    name,
                                    ref_prod,
                                    region,
                                    "not found",
                                )
                                continue

                            if supply_share > 0:

                                suppliers = get_shares_from_production_volume(
                                    potential_suppliers
                                )
                                new_excs.extend(
                                    self.write_suppliers_exchanges(
                                        suppliers, supply_share
                                    )
                                )

                        if len(new_excs) > 0:
                            total = 0
                            for exc in new_excs:
                                total += exc["amount"]
                            for exc in new_excs:
                                exc["amount"] /= total

                            new_market["exchanges"].extend(new_excs)

                            # check if we should add some additional exchanges
                            if "add" in market_vars:

                                for additional_exc in market_vars["add"]:

                                    add_excs = self.add_additional_exchanges(
                                        additional_exc, region
                                    )
                                    new_market["exchanges"].extend(add_excs)

                            # check if there are variables that
                            # relate to inefficiencies or losses

                            if "efficiency" in market_vars:
                                efficiency_data = self.external_scenarios_data[i][
                                    "efficiency"
                                ]
                                new_market = self.adjust_efficiency_of_new_markets(
                                    new_market, market_vars, region, efficiency_data
                                )

                            self.database.append(new_market)
                        else:
                            regions.remove(region)
                        # Loop through the technologies that should
                        # compose the market

                    # if so far, a market for `World` has not been created
                    # we need to create one then

                    create_world_region = True

                    if "World" in regions:
                        create_world_region = False

                    if "except regions" in market_vars:
                        if "World" in market_vars["except regions"]:
                            create_world_region = False

                    if create_world_region:

                        world_market = self.fill_in_world_market(
                            market_vars, regions, i, vars
                        )
                        self.database.append(world_market)

                    # if the new markets are meant to replace for other
                    # providers in the database

                    if "replaces" in market_vars:

                        self.relink_to_new_datasets(
                            replaces=market_vars["replaces"],
                            replaces_in=market_vars.get("replaces in", None),
                            new_name=market_vars["name"],
                            new_ref=market_vars["reference product"],
                            ratio=market_vars.get("replacement ratio", 1),
                            regions=regions,
                        )

    def relink_to_new_datasets(
        self,
        replaces: list,
        replaces_in: list,
        new_name: str,
        new_ref: str,
        ratio,
        regions: list,
    ) -> None:
        """
        Replaces exchanges that match `old_name` and `old_ref` with exchanges that
        have `new_name` and `new_ref`. The new exchange is from an IAM region, and so, if the
        region is not part of `regions`, we use `World` instead.

        :param old_name: `name` of the exchange to replace
        :param old_ref: `product` of the exchange to replace
        :param new_name: `name`of the new provider
        :param new_ref: `product` of the new provider
        :param regions: list of IAM regions the new provider can originate from

        """

        datasets = []

        if replaces_in:
            for k in replaces_in:
                list_fltr = []
                operator = k.get("operator", "equals")
                for field in ["name", "reference product", "location", "unit"]:
                    if field in k:
                        if field == "location":
                            list_fltr.append(ws.equals(field, k[field]))
                        else:
                            if operator == "equals":
                                list_fltr.append(ws.equals(field, k[field]))
                            else:
                                list_fltr.append(ws.contains(field, k[field]))

                            list_fltr.append(ws.contains(field, k[field]))
                datasets.extend(list(ws.get_many(self.database, *list_fltr)))
        else:
            datasets = self.database

        log = []

        list_fltr = []
        for k in replaces:
            fltr = []
            operator = k.get("operator", "equals")
            for field in ["name", "product", "location", "unit"]:
                if field in k:
                    if field == "location":
                        fltr.append(ws.equals(field, k[field]))
                    else:
                        if operator == "equals":
                            fltr.append(ws.equals(field, k[field]))
                        else:
                            fltr.append(ws.contains(field, k[field]))
            list_fltr.append(fltr)

        for dataset in datasets:
            filtered_exchanges = []
            for fltr in list_fltr:
                filtered_exchanges.extend(list(ws.technosphere(dataset, *fltr)))

            for exc in filtered_exchanges:
                if dataset["location"] in regions:
                    new_loc = dataset["location"]
                elif dataset["location"] == "World":
                    new_loc = "World"
                else:
                    new_loc = self.geo.ecoinvent_to_iam_location(dataset["location"])

                log.append(
                    [
                        dataset["name"],
                        dataset["reference product"],
                        dataset["location"],
                        exc["name"],
                        exc["product"],
                        exc["location"],
                        new_name,
                        new_ref,
                        new_loc,
                    ]
                )

                exc["name"] = new_name
                exc["product"] = new_ref
                exc["location"] = new_loc
                exc["amount"] *= ratio

                if "input" in exc:
                    del exc["input"]

        if log:
            with open(
                DATA_DIR
                / f"logs/external scenario - exchanges {self.scenario} {self.year}-{date.today()}.csv",
                "a",
                encoding="utf-8",
            ) as csv_file:
                writer = csv.writer(csv_file, delimiter=";", lineterminator="\n")
                writer.writerow(
                    [
                        "name",
                        "product",
                        "location",
                        "old supplier name",
                        "old supplier product",
                        "old supplier location",
                        "new supplier name",
                        "new supplier product",
                        "new supplier location",
                    ]
                )
                for line in log:
                    writer.writerow(line)