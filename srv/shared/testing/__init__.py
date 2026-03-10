"""
Backward-compatibility shim.

The canonical location for test utilities is now busibox_common.testing.
This module re-exports everything so that existing ``from testing import X``
imports continue to work during the migration period.
"""
from busibox_common.testing import *  # noqa: F401,F403
from busibox_common.testing import __all__  # noqa: F401
