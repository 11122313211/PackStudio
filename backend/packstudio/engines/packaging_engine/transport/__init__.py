"""Transport / distribution engineering helpers.

Currently this package holds the simplified ISTA 3A stacking estimate. All
numbers produced here are engineering estimates that require manufacturer
validation; they are not a substitute for lab-measured compressive strength.
"""

from packstudio.engines.packaging_engine.transport.ista import stacking_estimate

__all__ = ["stacking_estimate"]
