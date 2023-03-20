# ``premise``

<div style="text-align:center">
<img src="https://github.com/polca/premise/raw/master/docs/large.png" height="300"/>
</div>

# **PR**ospective **E**nviron**M**ental **I**mpact As**SE**ssment
## Coupling the ecoinvent database with projections from Integrated Assessment Models (IAM)


<p align="center">
  <a href="https://badge.fury.io/py/premise" target="_blank"><img src="https://badge.fury.io/py/premise.svg"></a>
  <a href="https://github.com/polca/premise" target="_blank"><img src="https://github.com/polca/premise/actions/workflows/main.yml/badge.svg?branch=master"></a>
  <a href="https://coveralls.io/github/polca/premise" target="_blank"><img src="https://coveralls.io/repos/github/polca/premise/badge.svg"></a>
  <a href="https://premise.readthedocs.io/en/latest/" target="_blank"><img src="https://readthedocs.org/projects/premise/badge/?version=latest"></a>
</p>
 
Previously named *rmnd-lca*. *rmnd-lca* was designed to work with the IAM model REMIND only.
As it now evolves towards a more IAM-neutral approach, a change of name was considered.

Scientific publication available here: [Sacchi et al, 2022](https://doi.org/10.1016/j.rser.2022.112311).

What's new in 1.5.0?
====================

-  Added support for ecoinvent 3.9 and 3.9.1
-  Added support for ecoinvent 3.8 and 3.9/3.9.1 consequential -- see [docs](https://premise.readthedocs.io/en/latest/consequential.html)
-  Added REMIND SSP1 and SSP5 scenarios -- see [docs](https://premise.readthedocs.io/en/latest/usage.html#remind-ssp1-and-ssp5)
-  Updated GAINS emission factors, using GAINS-EU and GAINS-IAM -- see [docs](https://premise.readthedocs.io/en/latest/usage.html#gains-emission-factors)
-  Added new inventories for DAC and DACCS -- see [docs](https://premise.readthedocs.io/en/latest/usage.html#dac-and-daccs)
-  Added new inventories for EPR and SMR nuclear reactors -- see [docs](https://premise.readthedocs.io/en/latest/usage.html#epr-and-smr-nuclear-reactors)
-  Made mapping to new IAM easier -- see [docs](https://premise.readthedocs.io/en/latest/usage.html#mapping-to-new-iam)

What's new in 1.3.0?
====================

-   Added support for user-generated scenarios (see [docs](https://premise.readthedocs.io/en/latest/user_scenarios.html) and [notebook](https://github.com/polca/premise/blob/master/examples/examples%20user-defined%20scenarios.ipynb))
-   Updated REMIND scenarios to REMIND v.3.0

What's new in 1.0.0?
====================

We have just released the first major version of *premise*.

Compared to 1.2, here are some of the most notable changes:

* *premise* works with ecoinvent 3.8 and 3.9, cut-off and consequential.
* emission factors for local air pollutants are updated, using GAINS-EU and GAINS-IAM
* region- and scenario-specific inventories for DAC and DACCS
* additional REMIND scenarios (SSP1 and SSP5)


Documentation
-------------
[https://premise.readthedocs.io/en/latest/](https://premise.readthedocs.io/en/latest/)

Objective
---------

The objective is to produce life cycle inventories under future energy policies, by modifying the inventory database
ecoinvent 3 to reflect projected energy policy trajectories.

Requirements
------------
* **Python 3.9**
* License for [ecoinvent 3][1]
* Some IAM output files come with the library and are located by default in the subdirectory "/data/iam_output_files". **If you wish to use
 those files, you need to request (by [email](mailto:romain.sacchi@psi.ch)) an encryption key from the developers**.
 A file path can be specified to fetch IAM output files elsewhere on your computer.
 * [brightway2][2] (optional)

How to install this package?
----------------------------

Two options:

A development version with the latest advancements (but with the risks of unseen bugs),
is available from Anaconda Cloud:

    
    conda config --append conda-forge
    conda config --append cmutel
    conda config --append romainsacchi
    conda install premise


For a more stable and proven version, from Pypi:

    pip install premise

will install the package and the required dependencies.


How to use it?
--------------

The best way is to follow [the examples from the Jupyter Notebook](https://github.com/polca/premise/blob/master/examples/examples.ipynb). 

# Support

Do not hesitate to contact the development team at [romain.sacchi@psi.ch](mailto:romain.sacchi@psi.ch)
or [aloisdir@pik-potsdam.de](mailto:aloisdir@pik-potsdam.de).

## Maintainers

* [Romain Sacchi](https://github.com/romainsacchi)
* [Alois Dirnaichner](https://github.com/Loisel)
* [Tom Mike Terlouw](https://github.com/tomterlouw)
* [Laurent Vandepaer](https://github.com/lvandepaer)
* [Chris Mutel](https://github.com/cmutel/)

## Contributing

See [contributing](https://github.com/polca/premise/blob/master/CONTRIBUTING.md).

## References

[1]:https://www.ecoinvent.org/
[2]:https://brightway.dev/

## License

[BSD-3-Clause](https://github.com/polca/premise/blob/master/LICENSE).
Copyright 2020 Potsdam Institute for Climate Impact Research, Paul Scherrer Institut.
