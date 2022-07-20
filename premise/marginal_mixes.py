"""
Calculate the marginal mix of a market in different ways.
"""

from functools import lru_cache
from typing import Tuple

import numpy as np
import xarray as xr
import yaml

from premise import DATA_DIR

# I've put the numbers I used for the paper in
# the lead times file, but there were a lot less
# technologies there, so the current file has a
# lot of placeholder values at the moment.
# TODO: I will update this later to get more accurate values for the lead times.

IAM_LEADTIMES = DATA_DIR / "consequential" / "leadtimes.yaml"
IAM_LIFETIMES = DATA_DIR / "consequential" / "lifetimes.yaml"


@lru_cache
def get_lifetime(list_tech: Tuple) -> np.ndarray:
    """
    Fetch lifetime values for different technologies from a .yaml file.
    :param list_tech: technology labels to find lifetime values for.
    :type list_tech: list
    :return: a numpy array with technology lifetime values
    :rtype: DataArray
    """
    with open(IAM_LIFETIMES, "r", encoding="utf-8") as stream:
        dict_ = yaml.safe_load(stream)

    dict_ = {k: v for k, v in dict_.items() if k in list_tech}

    return np.array(list(dict_.values()), dtype=float)


@lru_cache
def get_leadtime(list_tech: Tuple) -> np.ndarray:
    """
    Fetch leadtime values for different technologies from a .yaml file.
    :param list_tech: technology labels to find leadtime values for.
    :type list_tech: list
    :return: a numpy array with technology leadtime values
    :rtype: np.array
    """
    with open(IAM_LEADTIMES, "r", encoding="utf-8") as stream:
        dict_ = yaml.safe_load(stream)

    dict_ = {k: v for k, v in dict_.items() if k in list(list_tech)}

    return np.array(list(dict_.values()), dtype=float)


def fetch_avg_leadtime(leadtime: np.ndarray, shares: np.ndarray) -> int:
    """
    Calculate the average lead time of a market.
    """
    return (shares * leadtime).sum().astype(int).values.item(0)


def fetch_avg_capital_replacement_rate(avg_lifetime: int, data: xr.DataArray) -> float:
    """
    Calculate the average capital replacement rate of a market.
    """
    return (-1 / avg_lifetime * data.sum(dim="variables").values).item(0) or 0.0


def fetch_capital_replacement_rates(
    lifetime: np.ndarray, data: xr.DataArray
) -> np.ndarray:
    """
    Calculate the average capital replacement rate of a market.
    """
    return (-1 / lifetime * data).values


def fetch_avg_lifetime(lifetime: np.ndarray, shares: np.ndarray) -> int:
    """
    Calculate the average lifetime of a market.
    """
    return (shares * lifetime).sum().astype(int).values.item(0) or 30


def fetch_volume_change(data: xr.DataArray, start_year: int, end_year: int) -> float:
    """
    Calculate the volume change of a market.
    """
    return (
        (
            data.sel(year=end_year).sum(dim="variables")
            - data.sel(year=start_year).sum(dim="variables")
        )
        / (end_year - start_year)
    ).values


def remove_constrained_suppliers(data: xr.DataArray) -> xr.DataArray:
    """
    Remove the shares of suppliers that are constrained from the market.
    """

    # we set CHP suppliers to zero
    # as electricity production is not a
    # determining product for CHPs
    tech_to_ignore = ["CHP", "biomethane"]
    data.loc[
        dict(
            variables=[
                v for v in data.variables.values if any(x in v for x in tech_to_ignore)
            ],
        )
    ] = 0

    return data


def consequential_method(data: xr.DataArray, year: int, args: dict) -> xr.DataArray:

    """
    Used for consequential modeling only.
    Returns marginal market mixes
    according to the chosen method.

    If range_time and duration are None, then the lead time is taken as
    the time interval (just as with ecoinvent v.3.4).
    foresight: 0 = myopic, 1 = perfect foresight
    lead time: 0 = market average lead time is taken for all technologies,
    lead time: 1 = individual lead time for each technology.
    capital_repl_rate: 0 = horizontal baseline is used,
    capital_repl_rate: 1 = capital replacement rate is used as baseline.
    measurement: 0 = slope, 1 = linear regression,
    measurement: 2 = area under the curve, 3 = weighted slope,
    measurement: 4 = time interval is split in individual years and measured
    weighted_slope_start and end: is needed for measurement method 3,
    the number indicates where the short slope starts
    and ends and is given as the fraction of the total time interval.

    :param data: IAM data
    :param year: year to calculate the mix for
    :param args: arguments for the method

    :return: marginal market mixes
    """

    range_time: int = args.get("range time", 0)
    duration: int = args.get("duration", 0)
    foresight: bool = args.get("foresight", False)
    lead_time: int = args.get("lead time", False)
    capital_repl_rate: bool = args.get("capital replacement rate", False)
    measurement: int = args.get("measurement", 1)
    weighted_slope_start: float = args.get("weighted slope start", 0.75)
    weighted_slope_end: float = args.get("weighted slope end", 1.0)

    market_shares = xr.zeros_like(
        data.interp(year=[year]),
    )

    # as the time interval can be different for each technology
    # if the individual lead time is used,
    # I use DataArrays to store the values
    # (= start and end of the time interval)

    start = xr.zeros_like(market_shares)
    start.name = "start"
    end = xr.zeros_like(market_shares)
    end.name = "end"
    start_end = xr.merge([start, end])

    # Since there can be different start and end values,
    # I interpolated the entire data of the IAM instead
    # of doing it each time over
    minimum = min(data.year.values)
    maximum = max(data.year.values)
    years_to_interp_for = list(range(minimum, maximum + 1))
    data_full = data.interp(year=years_to_interp_for)

    techs = tuple(data_full.variables.values.tolist())
    leadtime = get_leadtime(techs)

    for region in data.coords["region"].values:

        # I don't yet know the exact start year
        # of the time interval, so as an approximation
        # I use for current_shares the start year
        # of the change
        shares = data_full.sel(region=region, year=year) / data_full.sel(
            region=region, year=year
        ).sum(dim="variables")

        time_parameters = {
            (False, False, False, False): {
                "start": year,
                "end": year + fetch_avg_leadtime(leadtime, shares),
                "start_avg": year,
                "end_avg": year + fetch_avg_lifetime(lifetime=leadtime, shares=shares),
            },
            (False, False, True, True): {
                "start": year - fetch_avg_leadtime(leadtime, shares),
                "end": year,
                "start_avg": year - fetch_avg_leadtime(leadtime, shares),
                "end_avg": year + fetch_avg_lifetime(lifetime=leadtime, shares=shares),
            },
            (False, False, False, True): {"start": year, "end": year + leadtime},
            (True, False, False, True): {
                "start": year + fetch_avg_leadtime(leadtime, shares) - range_time,
                "end": year + fetch_avg_leadtime(leadtime, shares) + range_time,
            },
            (True, False, True, False): {
                "start": year - range_time,
                "end": year + range_time,
            },
            (True, False, False, True): {
                "start": year + leadtime - range_time,
                "end": year + leadtime + range_time,
            },
            (True, False, True, True): {
                "start": year - range_time,
                "end": year + range_time,
            },
            (True, True, False, False): {
                "start": year + fetch_avg_leadtime(leadtime, shares),
                "end": year + fetch_avg_leadtime(leadtime, shares) + duration,
            },
            (True, True, True, False): {"start": year, "end": year + duration},
            (True, True, False, True): {
                "start": year + leadtime,
                "end": year + leadtime + duration,
            },
            (True, True, True, True): {"start": year, "end": year + duration},
        }

        try:

            start = time_parameters[
                (foresight, capital_repl_rate, lead_time, measurement)
            ]["start"]
            end = time_parameters[
                (foresight, capital_repl_rate, lead_time, measurement)
            ]["end"]

            avg_start = time_parameters[
                bool(range_time), bool(duration), bool(foresight), bool(lead_time)
            ]["start_avg"]
            avg_end = time_parameters[
                bool(range_time), bool(duration), bool(foresight), bool(lead_time)
            ]["end_avg"]

        except KeyError as err:
            raise KeyError(
                "The combination of range_time, duration, foresight, and lead_time "
                "is not possible. Please check your input."
            ) from err

        # Now that we do know the start year of the time interval,
        # we can use this to "more accurately" calculate the current shares
        shares = data_full.sel(region=region, year=avg_start) / data_full.sel(
            region=region, year=avg_start
        ).sum(dim="variables")

        # we first need to calculate the average capital replacement rate of the market
        # which is here defined as the inverse of the production-weighted average lifetime
        lifetime = get_lifetime(techs)

        # again was put in to deal with Nan values in data
        avg_lifetime = fetch_avg_lifetime(lifetime, shares)

        # again was put in to deal with Nan values in data
        avg_cap_repl_rate = fetch_avg_capital_replacement_rate(
            avg_lifetime, data_full.sel(region=region, year=avg_start)
        )

        volume_change = fetch_volume_change(
            data_full.sel(region=region), avg_start, avg_end
        )

        data_full = remove_constrained_suppliers(data_full)

        # second, we measure production growth
        # within the determined time interval
        # for each technology
        # using the selected measuring method and baseline
        if not capital_repl_rate and measurement == 0:
            # if the capital replacement rate is not used,

            # TODO: should be tech-specific start and end years

            market_shares.loc[dict(region=region)] = (
                (
                    data_full.sel(
                        region=region,
                        year=avg_end,
                    )
                    - data_full.sel(
                        region=region,
                        year=avg_start,
                    )
                )
                / (avg_end - avg_start)
            ).values[:, None]

        if not capital_repl_rate and measurement == 1:

            coeff_a = data_full.sel(region=region).where(
                data_full.sel(region=region).year >= avg_start
            )
            coeff_b = coeff_a.where(coeff_a.year <= avg_end)
            coeff_c = coeff_b.polyfit(dim="year", deg=1)

            market_shares.loc[dict(region=region)] = coeff_c.polyfit_coefficients[
                0
            ].values[:, None]

        if not capital_repl_rate and measurement == 2:

            coeff_a = data_full.sel(region=region).where(
                data_full.sel(region=region).year >= avg_start
            )
            coeff_b = coeff_a.where(coeff_a.year <= avg_end)
            coeff_c = coeff_b.sum(dim="year").values
            n = avg_end - avg_start

            total_area = 0.5 * (
                2 * coeff_c
                - data_full.sel(
                    region=region,
                    year=avg_end,
                )
                - data_full.sel(
                    region=region,
                    year=avg_start,
                )
            )
            baseline_area = (
                data_full.sel(
                    region=region,
                    year=avg_start,
                )
                * n
            )
            # TODO: divide this by the time interval `n`
            market_shares.loc[dict(region=region)] = (
                total_area - baseline_area
            ).values[:, None]

        if not capital_repl_rate and measurement == 3:

            slope = (
                data_full.sel(
                    region=region,
                    year=avg_end,
                ).values
                - data_full.sel(
                    region=region,
                    year=avg_start,
                ).values
            ) / (avg_end - avg_start)

            short_slope_start = int(
                avg_start + (avg_end - avg_start) * weighted_slope_start
            )
            short_slope_end = int(
                avg_start + (avg_end - avg_start) * weighted_slope_end
            )
            short_slope = (
                data_full.sel(region=region, year=short_slope_end).values
                - data_full.sel(region=region, year=short_slope_start).values
            ) / (short_slope_end - short_slope_start)

            x = np.where(slope == 0, 0, slope / short_slope)
            split_year = np.where(x < 0, -1, 1)
            split_year = np.where(
                (x > -500) & (x < 500),
                2 * (np.exp(-1 + x) / (1 + np.exp(-1 + x)) - 0.5),
                split_year,
            )

            market_shares.loc[dict(region=region)] = (slope + slope * split_year)[
                :, None
            ]

        if not capital_repl_rate and measurement == 4:
            n = avg_end - avg_start
            # use average start and end years
            split_years = range(avg_start, avg_end)
            for split_year in split_years:
                market_shares_split = xr.zeros_like(market_shares)
                market_shares_split.loc[dict(region=region)] = (
                    data_full.sel(region=region, year=split_year + 1)
                    - data_full.sel(region=region, year=split_year)
                ).values[:, None]

                # we remove NaNs and np.inf
                market_shares_split.loc[dict(region=region)].values[
                    market_shares_split.loc[dict(region=region)].values == np.inf
                ] = 0
                market_shares_split.loc[dict(region=region)] = market_shares_split.loc[
                    dict(region=region)
                ].fillna(0)

                if volume_change < 0:
                    # we remove suppliers with a positive growth
                    market_shares.loc[dict(region=region)].values[
                        market_shares.loc[dict(region=region)].values > 0
                    ] = 0
                    # we reverse the sign of negative growth suppliers
                    market_shares.loc[dict(region=region)] *= -1
                    market_shares.loc[dict(region=region)] /= market_shares.loc[
                        dict(region=region)
                    ].sum(dim="variables")

                else:
                    # we remove suppliers with a negative growth
                    market_shares_split.loc[dict(region=region)].values[
                        market_shares_split.loc[dict(region=region)].values < 0
                    ] = 0
                    market_shares_split.loc[
                        dict(region=region)
                    ] /= market_shares_split.loc[dict(region=region)].sum(
                        dim="variables"
                    )

                market_shares.loc[dict(region=region)] += market_shares_split.loc[
                    dict(region=region)
                ]
            market_shares.loc[dict(region=region)] /= n

        if capital_repl_rate and measurement == 0:
            # same as above for measurement 0, 1, 3, 4
            market_shares.loc[dict(region=region)] = (
                (
                    data_full.sel(
                        region=region,
                        year=avg_end,
                    ).values
                    - data_full.sel(
                        region=region,
                        year=avg_start,
                    ).values
                )
                / (avg_end - avg_start)
            )[:, None]

            # get the capital replacement rate
            # which is here defined as -1 / lifetime
            cap_repl_rate = fetch_capital_replacement_rates(
                lifetime, data_full.sel(region=region, year=avg_start)
            )

            # subtract the capital replacement (which is negative) rate
            # to the changes market share

            market_shares.loc[dict(region=region)] -= cap_repl_rate[:, None]

        if capital_repl_rate and measurement == 1:
            coeff_a = data_full.sel(region=region).where(
                data_full.sel(region=region).year >= avg_start
            )
            coeff_b = coeff_a.where(coeff_a.year <= avg_end)
            coeff_c = coeff_b.polyfit(dim="year", deg=1)
            market_shares.loc[dict(region=region)] = coeff_c.polyfit_coefficients[
                0
            ].values[:, None]

            # get the capital replacement rate
            # which is here defined as -1 / lifetime
            cap_repl_rate = fetch_capital_replacement_rates(
                lifetime, data_full.sel(region=region, year=avg_start)
            )

            # subtract the capital replacement (which is negative) rate
            # to the changes market share
            market_shares.loc[dict(region=region)] -= cap_repl_rate[:, None]

        if capital_repl_rate and measurement == 2:

            coeff_a = data_full.sel(region=region).where(
                data_full.sel(region=region).year >= avg_start
            )
            coeff_b = coeff_a.where(coeff_a.year <= avg_end)
            coeff_c = coeff_b.sum(dim="year").values
            n = avg_end - avg_start

            total_area = 0.5 * (
                2 * coeff_c
                - data_full.sel(
                    region=region,
                    year=avg_end,
                ).values
                - data_full.sel(
                    region=region,
                    year=avg_start,
                ).values
            )
            baseline_area = (
                data_full.sel(
                    region=region,
                    year=avg_start,
                ).values
                * n
            )
            market_shares.loc[dict(region=region)] = (total_area - baseline_area)[
                :, None
            ]

            # this bit differs from above
            # get the capital replacement rate
            # which is here defined as -1 / lifetime
            cap_repl_rate = (
                fetch_capital_replacement_rates(
                    lifetime, data_full.sel(region=region, year=avg_start)
                )
                * ((avg_end - avg_start) ^ 2)
                * 0.5
            )

            # subtract the capital replacement (which is negative) rate
            # to the changes market share
            market_shares.loc[dict(region=region)] -= cap_repl_rate[:, None]

        if capital_repl_rate and measurement == 3:
            slope = (
                data_full.sel(
                    region=region,
                    year=avg_end,
                ).values
                - data_full.sel(
                    region=region,
                    year=avg_start,
                ).values
            ) / (avg_end - avg_start)

            short_slope_start = int(
                avg_start + (avg_end - avg_start) * weighted_slope_start
            )

            short_slope_end = int(
                avg_start + (avg_end - avg_start) * weighted_slope_end
            )

            short_slope = (
                data_full.sel(region=region, year=short_slope_end)
                - data_full.sel(region=region, year=short_slope_start)
            ) / (short_slope_end - short_slope_start)[:, None]

            cap_repl_rate = fetch_capital_replacement_rates(
                lifetime, data_full.sel(region=region, year=avg_start)
            )
            slope -= cap_repl_rate[:, None]
            short_slope -= cap_repl_rate[:, None]

            x = np.where(slope == 0, 0, slope / short_slope)
            split_year = np.where(x < 0, -1, 1)
            split_year = np.where(
                (x > -500) & (x < 500),
                2 * (np.exp(-1 + x) / (1 + np.exp(-1 + x)) - 0.5),
                split_year,
            )

            market_shares.loc[dict(region=region)] = slope + slope * split_year

        if capital_repl_rate and measurement == 4:
            n = avg_end - avg_start
            split_years = list(
                range(
                    avg_start,
                    avg_end,
                )
            )
            for split_year in split_years:
                market_shares_split = xr.zeros_like(market_shares)

                market_shares_split.loc[dict(region=region)] = (
                    data_full.sel(region=region, year=split_year + 1)
                    - data_full.sel(region=region, year=split_year)
                ).values[:, None]

                cap_repl_rate = fetch_capital_replacement_rates(
                    lifetime, data_full.sel(region=region, year=avg_start)
                )

                max_cap_repl_rate = (
                    data_full.sel(
                        region=region,
                        year=avg_start,
                    ).values
                    / n
                )

                cap_repl_rate = np.clip(cap_repl_rate, None, max_cap_repl_rate)

                market_shares_split.loc[dict(region=region)] -= cap_repl_rate[:, None]

                # we remove NaNs and np.inf
                market_shares_split.loc[dict(region=region)].values[
                    market_shares_split.loc[dict(region=region)].values == np.inf
                ] = 0
                market_shares_split.loc[dict(region=region)] = market_shares_split.loc[
                    dict(region=region)
                ].fillna(0)

                if volume_change < avg_cap_repl_rate:
                    # we remove suppliers with a positive growth
                    market_shares.loc[dict(region=region)].values[
                        market_shares.loc[dict(region=region)].values > 0
                    ] = 0
                    # we reverse the sign of negative growth suppliers
                    market_shares.loc[dict(region=region)] *= -1
                    market_shares.loc[dict(region=region)] /= market_shares.loc[
                        dict(region=region)
                    ].sum(dim="variables")
                else:
                    # we remove suppliers with a negative growth
                    market_shares_split.loc[dict(region=region)].values[
                        market_shares_split.loc[dict(region=region)].values < 0
                    ] = 0
                    market_shares_split.loc[
                        dict(region=region)
                    ] /= market_shares_split.loc[dict(region=region)].sum(
                        dim="variables"
                    )
                market_shares.loc[dict(region=region)] += market_shares_split.loc[
                    dict(region=region)
                ]

            market_shares.loc[dict(region=region)] /= n

        market_shares.loc[dict(region=region)] = market_shares.loc[
            dict(region=region)
        ].round(3)

        # we remove NaNs and np.inf
        market_shares.loc[dict(region=region)].values[
            market_shares.loc[dict(region=region)].values == np.inf
        ] = 0
        market_shares.loc[dict(region=region)] = market_shares.loc[
            dict(region=region)
        ].fillna(0)

        # market decreasing faster than the average capital renewal rate
        # in this case, the idea is that oldest/non-competitive technologies
        # are likely to supply by increasing their lifetime
        # as the market does not justify additional capacity installation
        if (not capital_repl_rate and volume_change < 0) or (
            capital_repl_rate and volume_change < avg_cap_repl_rate
        ):
            # we remove suppliers with a positive growth
            market_shares.loc[dict(region=region)].values[
                market_shares.loc[dict(region=region)].values > 0
            ] = 0
            # we reverse the sign of negative growth suppliers
            market_shares.loc[dict(region=region)] *= -1
            market_shares.loc[dict(region=region)] /= market_shares.loc[
                dict(region=region)
            ].sum(dim="variables")
        # increasing market or
        # market decreasing slowlier than the
        # capital renewal rate
        else:
            # we remove suppliers with a negative growth
            market_shares.loc[dict(region=region)].values[
                market_shares.loc[dict(region=region)].values < 0
            ] = 0
            market_shares.loc[dict(region=region)] /= market_shares.loc[
                dict(region=region)
            ].sum(dim="variables")

    return market_shares
