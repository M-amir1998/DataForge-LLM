"""Pipeline graph, stage contracts, execution, and the run service.

Import the run service from :mod:`dataforge.pipelines.runner`; this package deliberately
does not import it eagerly so that stage modules stay importable without the reporting
layer.
"""
