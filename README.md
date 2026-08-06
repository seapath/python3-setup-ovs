[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=seapath_python3-setup-ovs&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=seapath_python3-setup-ovs)
# python3-setup-ovs
Python tool to setup the ovs topology in a Seapath cluster

## Tests

The test suite runs entirely off-target: every call to OVS, to the network
stack and to sysfs is mocked, so no cluster, no root access and no real
Open vSwitch are needed.

```sh
pip install -e ".[test]"
pytest
```

To reproduce the coverage figures the CI publishes in its run summary:

```sh
pytest --cov=setup_ovs --cov-report=term-missing --cov-report=xml
```

Branch coverage is enabled in `pyproject.toml`, so the report covers both
the statement and the branch criteria.

A handful of tests are marked `xfail(strict=True)`. Each one documents a bug
found while writing the suite and pins the current, wrong behaviour: the
suite fails again the day the bug is fixed, which forces the marker to be
removed along with the fix. Their `reason` field states the defect.

## Reproducible build

The wheel is byte-for-byte reproducible provided `SOURCE_DATE_EPOCH` is set.
Without it setuptools stamps the archive with the source file mtimes, which
differ on every checkout:

```sh
SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct) python -m build --wheel
```

The `reproducible-build` CI job builds the wheel twice this way and compares
the SHA-256 of the two archives.
