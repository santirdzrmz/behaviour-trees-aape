# Behaviour Trees for Autonomous Agents (AAPE)

Behaviour trees for agents in AAPE, the Unity-based simulator used in the Autonomous Agents course at the Universitat Autònoma de Barcelona (2026).

The main tree, `BTCollect.py`, drives an astronaut in the "Alone" scenario. She wanders the outpost, uses ray sensors to spot alien flowers and collect them, and carries at most 2 at a time. When her inventory is full she teleports back to base, drops the flowers and resumes searching. The root is a reactive selector that re-evaluates priorities on every tick:

```
Root: Selector (memory=False)
├── FullInventoryHandler: Sequence
│   ├── CheckInventoryFull
│   └── ReturnAndUnload: TeleportToBase → LeaveFlowers
└── SearchAndCollect: Selector
    ├── CollectInSight: CheckFlowerInSight → MoveTowardFlower
    └── Wander
```

## Files

| File | What it is |
|---|---|
| `BTCollect.py` | The flower-collecting behaviour tree |
| `Goals_BT_Basic.py` | Basic goal behaviours: move forward, move a set distance, turn, idle |
| `AAgent_BT.py` | Agent client: connects to the simulator over WebSocket and ticks the tree |
| `Sensors.py` | Ray-perception sensor helpers |
| `Spawner.py` | Spawns a pack of agents from a pack file |
| `AAgent-*.json`, `APack*.json` | Agent and pack configurations |

The simulator itself is course material and is not included. A ready-to-download zip of this code is attached to the latest release.
