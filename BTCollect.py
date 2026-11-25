"""
BTCollect.py
============
Behaviour tree for the Astronaut agent in the "Alone" scenario of the AAPE
Behaviour Trees assignment.

Overall mission
---------------
The Astronaut wanders the 3-AAC outpost collecting alien flowers
(tag: 'AlienFlower'). She can carry at most 2 flowers at once. When her
inventory is full she teleports back to BaseAlpha, drops the flowers, and
resumes searching.

Tree structure
--------------
Root: Selector (memory=False)          <-- re-evaluates priorities every tick
├── FullInventoryHandler: Sequence (memory=True)
│   ├── [COND] CheckInventoryFull      <-- SUCCESS when carrying >= 2 flowers
│   └── ReturnAndUnload: Sequence (memory=True)
│       ├── TeleportToBase             <-- teleport to BaseAlpha, wait for container
│       └── LeaveFlowers               <-- drop all flowers at container
└── SearchAndCollect: Selector (memory=False)  <-- reactive: checks sight first
    ├── CollectInSight: Sequence (memory=True)
    │   ├── [COND] CheckFlowerInSight  <-- SUCCESS when a flower is in sensor range
    │   └── MoveTowardFlower           <-- steer toward flower, collect on contact
    └── Wander                         <-- fallback: random walk, avoid obstacles

Sensor configuration (AAgent-Alpha.json)
-----------------------------------------
ray_perception_sensor_param: [6, 90, 0, 30]
  rays_per_direction : 6   -> 13 rays total (6 left + 1 centre + 6 right)
  max_ray_degrees    : 90  -> +-90 degree FOV (agent can see sideways)
  sphere_cast_radius : 0
  ray_length         : 30  -> rays extend 30 Unity units

Ray layout (index: angle):
  0:-90  1:-75  2:-60  3:-45  4:-30  5:-15  6:0  7:+15  8:+30  9:+45  10:+60  11:+75  12:+90

Design notes
------------
* With +-90 degrees FOV the sensor covers the full hemisphere in front and
  beside the agent. Flowers to the side are detected immediately.
* Wander avoids obstacles only when a ray in the FORWARD CONE (indices 4-8,
  covering +-30 degrees) hits something within AVOID_DISTANCE (5 units).
  Outer rays (0-3, 9-12) seeing rocks to the side trigger no turn, so the
  agent threads into rock clusters where flowers are hidden.
* MoveTowardFlower minimises turning: it only issues a turn command when the
  flower angle exceeds 20 degrees, keeping the agent moving in long straight
  bursts rather than constantly micro-correcting.
"""

import asyncio
import math
import random

import py_trees as pt

import Sensors


# ---------------------------------------------------------------------------
# Condition nodes
# ---------------------------------------------------------------------------

class CheckInventoryFull(pt.behaviour.Behaviour):
    """
    Condition — checks whether the Astronaut's inventory is full.

    Returns SUCCESS when carrying >= 2 AlienFlowers, FAILURE otherwise.
    Placed first in the root Selector so the return-to-base branch is always
    evaluated before any search logic.
    """

    def __init__(self, name, a_agent):
        super().__init__(name)
        self.a_agent = a_agent

    def update(self):
        flower_amount = next(
            (item['amount'] for item in self.a_agent.i_state.myInventoryList
             if item['name'] == 'AlienFlower'),
            0
        )
        return (pt.common.Status.SUCCESS
                if flower_amount >= 2
                else pt.common.Status.FAILURE)


class CheckFlowerInSight(pt.behaviour.Behaviour):
    """
    Condition — checks whether the ray-cast sensor currently detects a flower.

    With +-90 degree FOV this covers the full lateral hemisphere, so a flower
    anywhere beside or in front of the agent will trigger SUCCESS.

    Returns SUCCESS when at least one ray hits an object tagged 'AlienFlower',
    FAILURE when no flower is visible.
    """

    def __init__(self, name, a_agent):
        super().__init__(name)
        self.a_agent = a_agent

    def update(self):
        for ray in zip(*self.a_agent.rc_sensor.sensor_rays):
            if (ray[Sensors.RayCastSensor.HIT] and
                    ray[Sensors.RayCastSensor.OBJECT_INFO] is not None and
                    ray[Sensors.RayCastSensor.OBJECT_INFO].get('tag') == 'AlienFlower'):
                return pt.common.Status.SUCCESS
        return pt.common.Status.FAILURE


# ---------------------------------------------------------------------------
# Action nodes
# ---------------------------------------------------------------------------

class TeleportToBase(pt.behaviour.Behaviour):
    """
    Action — teleports the Astronaut to BaseAlpha and waits until she is
    close enough to interact with the container.

    Returns RUNNING while waiting, SUCCESS on arrival, FAILURE on timeout.
    """

    TIMEOUT = 10.0

    def __init__(self, name, a_agent, base_name="BaseAlpha"):
        super().__init__(name)
        self.a_agent = a_agent
        self.base_name = base_name
        self.my_task = None

    def initialise(self):
        print(f"[TeleportToBase] Teleporting to {self.base_name}")
        self.my_task = asyncio.create_task(self._teleport())

    async def _teleport(self):
        try:
            await self.a_agent.send_message("action", f"teleport_to,{self.base_name}")
            elapsed = 0.0
            while not self.a_agent.i_state.nearbyContainerInventory:
                await asyncio.sleep(0.2)
                elapsed += 0.2
                if elapsed >= self.TIMEOUT:
                    print("[TeleportToBase] Timeout waiting for container proximity")
                    return False
            print(f"[TeleportToBase] Arrived at {self.base_name}")
            return True
        except asyncio.CancelledError:
            return False

    def update(self):
        if not self.my_task.done():
            return pt.common.Status.RUNNING
        return (pt.common.Status.SUCCESS
                if self.my_task.result()
                else pt.common.Status.FAILURE)

    def terminate(self, new_status):
        self.my_task.cancel()


class LeaveFlowers(pt.behaviour.Behaviour):
    """
    Action — deposits all AlienFlowers from the Astronaut's inventory into
    the nearby container.

    Returns RUNNING while waiting, SUCCESS on deposit, FAILURE if no container.
    """

    def __init__(self, name, a_agent):
        super().__init__(name)
        self.a_agent = a_agent
        self.my_task = None

    def initialise(self):
        print("[LeaveFlowers] Leaving flowers at container")
        self.my_task = asyncio.create_task(self._leave())

    async def _leave(self):
        try:
            if not self.a_agent.i_state.nearbyContainerInventory:
                print("[LeaveFlowers] No container nearby")
                return False
            flower_amount = next(
                (item['amount'] for item in self.a_agent.i_state.myInventoryList
                 if item['name'] == 'AlienFlower'),
                0
            )
            if flower_amount == 0:
                return True
            await self.a_agent.send_message("action", f"leave,AlienFlower,{flower_amount}")
            await asyncio.sleep(0.5)
            print(f"[LeaveFlowers] Left {flower_amount} flower(s)")
            return True
        except asyncio.CancelledError:
            return False

    def update(self):
        if not self.my_task.done():
            return pt.common.Status.RUNNING
        return (pt.common.Status.SUCCESS
                if self.my_task.result()
                else pt.common.Status.FAILURE)

    def terminate(self, new_status):
        self.my_task.cancel()


class MoveTowardFlower(pt.behaviour.Behaviour):
    """
    Action — steers the Astronaut toward the nearest detected flower.

    Moves forward continuously. Only issues a turn command when the flower
    has drifted more than ANGLE_THRESHOLD degrees from centre, producing long
    straight approach bursts with infrequent corrections.

    With +-90 degree FOV a flower detected on a side ray (e.g. -75 degrees)
    will trigger a turn toward it, bringing it into the forward cone quickly.

    Returns RUNNING while approaching, SUCCESS when the flower leaves the
    sensor (collected or momentarily lost).
    """

    # Degrees from centre before a steering correction is issued.
    # Wide enough to avoid micro-corrections but narrow enough to track
    # flowers detected on the side rays from the +-90 degree FOV.
    ANGLE_THRESHOLD = 20.0

    def __init__(self, name, a_agent):
        super().__init__(name)
        self.a_agent = a_agent
        self.my_task = None

    def initialise(self):
        print("[MoveTowardFlower] Heading toward detected flower")
        self.my_task = asyncio.create_task(self._move())

    async def _move(self):
        """
        Approach loop. Finds the flower ray closest to straight ahead
        (smallest absolute angle) and steers only when it drifts beyond
        ANGLE_THRESHOLD. Commands are sent on state transitions only.
        """
        try:
            currently_turning = False
            await self.a_agent.send_message("action", "mf")

            while True:
                # Find the flower ray with the smallest absolute angle
                flower_angle = None
                for ray in zip(*self.a_agent.rc_sensor.sensor_rays):
                    if (ray[Sensors.RayCastSensor.HIT] and
                            ray[Sensors.RayCastSensor.OBJECT_INFO] is not None and
                            ray[Sensors.RayCastSensor.OBJECT_INFO].get('tag') == 'AlienFlower'):
                        angle = ray[Sensors.RayCastSensor.ANGLE]
                        if flower_angle is None or abs(angle) < abs(flower_angle):
                            flower_angle = angle

                if flower_angle is None:
                    await self.a_agent.send_message("action", "stop")
                    print("[MoveTowardFlower] Flower no longer in sight — collected or lost")
                    return True

                if flower_angle > self.ANGLE_THRESHOLD:
                    if not currently_turning:
                        await self.a_agent.send_message("action", "tr")
                        currently_turning = True
                elif flower_angle < -self.ANGLE_THRESHOLD:
                    if not currently_turning:
                        await self.a_agent.send_message("action", "tl")
                        currently_turning = True
                else:
                    if currently_turning:
                        await self.a_agent.send_message("action", "nt")
                        await self.a_agent.send_message("action", "mf")
                        currently_turning = False

                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            pass

    def update(self):
        if not self.my_task.done():
            return pt.common.Status.RUNNING
        return (pt.common.Status.SUCCESS
                if self.my_task.result()
                else pt.common.Status.FAILURE)

    def terminate(self, new_status):
        self.my_task.cancel()


class Wander(pt.behaviour.Behaviour):
    """
    Action — random walk fallback when no flower is in sight.

    Moves forward continuously. Avoidance only triggers when a CENTER ray
    hits something within AVOID_DISTANCE — outer side rays are ignored so
    the agent threads into rock gaps where flowers are hidden.

    Sensor layout (17 rays, +-90 degrees, 7.5 degrees between rays):

    CENTER_RAYS [6, 7, 8, 9, 10] covers +-30 degrees — the genuine forward
    cone where a hit means the path is blocked. Outer rays (0-5, 11-16) at
    +-45 to +-90 degrees detect rocks to the side and are ignored for
    avoidance so the agent walks close to them.

    AVOID_DISTANCE (5 units) means the agent only reacts to obstacles that
    are truly close, ignoring distant rocks detected by the 30-unit rays.

    Returns RUNNING always — the parent Selector re-evaluates CheckFlowerInSight
    on every tick so the agent reacts the moment a flower enters the FOV.
    """

    # Forward cone ray indices — a hit here within AVOID_DISTANCE triggers a turn.
    # Covers +-30 degrees (indices 6-10) out of the full +-90 degree FOV.
    # Outer rays (0-5, 11-16) are intentionally excluded.
    CENTER_RAYS = [6, 7, 8, 9, 10]

    # Normal avoidance distance (Unity units) — triggers a turn when the commit
    # window is not active. Rocks/walls further away are ignored.
    AVOID_DISTANCE = 5.0

    # Hard-stop distance — triggers a turn even during the commit window.
    # Prevents the agent from walking straight into a wall or rock face while
    # committed to a heading. Should be less than AVOID_DISTANCE.
    HARD_STOP_DISTANCE = 2.0

    # Random turn duration when a center-zone obstacle triggers avoidance.
    TURN_TIME_MIN = 0.5   # seconds
    TURN_TIME_MAX = 1.5   # seconds

    # After each avoidance turn, the agent commits to moving forward for at
    # least this many seconds before obstacle avoidance is re-enabled.
    # This forces longer excursions away from the local area instead of
    # bouncing back and forth between nearby rocks.
    COMMIT_TIME = 2.0     # seconds

    # World-space (x, z) coordinates of the map centre — flowers are denser here.
    MAP_CENTER = (0.0, 0.0)

    # Probability [0.0-1.0] that an avoidance turn faces toward MAP_CENTER.
    # 0.75 means 3 out of 4 turns drift the agent back to the productive centre;
    # the remaining 0.25 keeps edge exploration alive so the full map is covered.
    CENTER_BIAS = 0.85

    def __init__(self, name, a_agent):
        super().__init__(name)
        self.a_agent = a_agent
        self.my_task = None

    def initialise(self):
        print("[Wander] Starting to wander")
        self.my_task = asyncio.create_task(self._wander())

    def _center_turn_dir(self):
        """
        Returns "tr" or "tl" biased toward MAP_CENTER.

        Computes the bearing from the agent's current position to the map
        centre using i_state.position (x, z) and i_state.rotation (y = yaw).

        Unity yaw convention: 0 degrees = facing +z, increases clockwise.
        atan2(dx, dz) with x before z matches this convention directly.

        A positive yaw difference means centre is to the right ("tr");
        negative means centre is to the left ("tl"). With probability
        CENTER_BIAS the center-facing direction is chosen; otherwise the
        opposite is chosen to maintain some edge exploration.
        """
        pos = self.a_agent.i_state.position
        rot = self.a_agent.i_state.rotation

        dx = self.MAP_CENTER[0] - pos['x']
        dz = self.MAP_CENTER[1] - pos['z']

        # Bearing to center in Unity degrees (clockwise from +z)
        bearing_to_center = math.degrees(math.atan2(dx, dz))

        # Signed angular difference: positive = center is to the right
        diff = (bearing_to_center - rot['y'] + 180) % 360 - 180

        toward_center = "tr" if diff > 0 else "tl"
        away_from_center = "tl" if diff > 0 else "tr"

        return toward_center if random.random() < self.CENTER_BIAS else away_from_center

    async def _wander(self):
        """
        Move forward continuously with committed straight-line excursions.

        After each avoidance turn the agent ignores new obstacle triggers for
        COMMIT_TIME seconds. This prevents the agent from bouncing between
        nearby rocks in a tight local area — it must push forward into new
        territory before it is allowed to turn again. Only a direct head-on
        collision (center ray, within AVOID_DISTANCE) can interrupt a committed
        run, and only after the commit window expires.

        "mf" is sent once at the start and only re-sent after each avoidance
        turn — Unity sustains forward motion between events at full speed.
        """
        try:
            await self.a_agent.send_message("action", "mf")
            commit_remaining = 0.0  # seconds left in the current committed run

            while True:
                sensor_hits = self.a_agent.rc_sensor.sensor_rays[Sensors.RayCastSensor.HIT]
                distances   = self.a_agent.rc_sensor.sensor_rays[Sensors.RayCastSensor.DISTANCE]

                # Two-tier avoidance:
                # Tier 1 — HARD_STOP: fires even during commit, prevents walking
                #           into walls/rocks at point-blank range.
                # Tier 2 — AVOID_DISTANCE: normal avoidance, only when commit
                #           window has expired so the agent pushes far enough
                #           before being allowed to turn again.
                hard_blocked = any(sensor_hits[i] == 1 and 0 < distances[i] <= self.HARD_STOP_DISTANCE
                                   for i in self.CENTER_RAYS)
                soft_blocked = any(sensor_hits[i] == 1 and 0 < distances[i] <= self.AVOID_DISTANCE
                                   for i in self.CENTER_RAYS)

                if hard_blocked or (soft_blocked and commit_remaining <= 0):
                    # Turn toward centre, restart committed run
                    await self.a_agent.send_message("action", "ntm")
                    turn_dir = self._center_turn_dir()
                    await self.a_agent.send_message("action", turn_dir)
                    await asyncio.sleep(random.uniform(self.TURN_TIME_MIN,
                                                       self.TURN_TIME_MAX))
                    await self.a_agent.send_message("action", "nt")
                    await self.a_agent.send_message("action", "mf")
                    commit_remaining = self.COMMIT_TIME
                elif commit_remaining > 0:
                    commit_remaining = max(0.0, commit_remaining - 0.1)

                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            # Do NOT send "stop" — successor node sends its own command
            # immediately, and a "stop" here would race with and silence it.
            pass

    def update(self):
        if self.my_task is not None and self.my_task.done():
            return pt.common.Status.FAILURE
        return pt.common.Status.RUNNING

    def terminate(self, new_status):
        if self.my_task is not None:
            self.my_task.cancel()


# ---------------------------------------------------------------------------
# Behaviour-tree wrapper
# ---------------------------------------------------------------------------

class BTCollect:
    """
    Wrapper class that builds and owns the py_trees BehaviourTree instance.

    Called by AAgent_BT.py via:
      - tick()              : advance the tree by one step each main-loop iteration
      - stop_behaviour_tree(): cleanly shut down all running nodes when switching away
    """

    def __init__(self, a_agent):
        self.a_agent = a_agent

        # --- Full-inventory branch ---
        return_seq = pt.composites.Sequence(name="ReturnAndUnload", memory=True)
        return_seq.add_children([
            TeleportToBase("TeleportToBase", a_agent),
            LeaveFlowers("LeaveFlowers", a_agent),
        ])

        full_inv_seq = pt.composites.Sequence(name="FullInventoryHandler", memory=True)
        full_inv_seq.add_children([
            CheckInventoryFull("CheckInventoryFull", a_agent),
            return_seq,
        ])

        # --- Search-and-collect branch ---
        # memory=False on SearchAndCollect ensures CheckFlowerInSight is
        # re-evaluated every tick even while Wander is running.
        collect_seq = pt.composites.Sequence(name="CollectInSight", memory=True)
        collect_seq.add_children([
            CheckFlowerInSight("CheckFlowerInSight", a_agent),
            MoveTowardFlower("MoveTowardFlower", a_agent),
        ])

        search_sel = pt.composites.Selector(name="SearchAndCollect", memory=False)
        search_sel.add_children([
            collect_seq,
            Wander("Wander", a_agent),
        ])

        # --- Root ---
        root = pt.composites.Selector(name="Root", memory=False)
        root.add_children([full_inv_seq, search_sel])

        self.behaviour_tree = pt.trees.BehaviourTree(root)
        self.behaviour_tree.setup()

    async def tick(self):
        """Advance the tree by one tick at ~20 ticks/second."""
        self.behaviour_tree.tick()
        await asyncio.sleep(0.05)

    def stop_behaviour_tree(self):
        """Stop all running nodes cleanly when switching away from this BT."""
        if self.behaviour_tree.root.status == pt.common.Status.RUNNING:
            self.behaviour_tree.root.stop(pt.common.Status.INVALID)
