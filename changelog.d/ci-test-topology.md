### Changed

**CI test execution is now explicitly planned and collection-conserving.** A
checked-in planner records disjoint unit, serial subprocess/CLI, and integration
contract lanes; canonical collection and final-outcome reports prevent sharding
from dropping or duplicating tests. Bare `pytest`, production defaults, and all
focused native-platform selections remain unchanged.
