# Speed Limit Sources and Overspeed Review

An overspeed event is only as good as the speed limit it was compared with. The speed comes from the vehicle or the GPS and is reliable. The limit comes from a map lookup and can be wrong, old or missing. This guide explains where limits come from, how the platform treats the doubtful ones, and how a reviewer decides.

## The three sources

Every overspeed event records its `limitSource`:

- `osm`: open map data. Free and broadly good, but community edited, so a rare road can carry a wrong value.
- `here`: a commercial map service. Usually accurate, and it can also report a road class limit alongside the posted one.
- `assumed_urban`: no usable limit was found, so the platform assumed an urban limit of 50 km/h.

## Age and held limits

`limitAgeS` is how old the limit was, in seconds, at the moment of the event. A fresh lookup has an age of a few seconds. When the lookup fails, for example in a tunnel or in a dead zone, the platform keeps using the last known limit. That value is exposed as `heldLimitKph`, and its age keeps growing.

## When the limit is not trusted

The platform applies two checks before it believes the limit.

**Held or stale limits.** A limit is held (`heldLimitKph` is set) or stale (`limitAgeS` over 60 s). The vehicle may have turned onto another road since the lookup, so the old limit may no longer apply. The event is re-judged against the assumed urban limit of 50 km/h and is kept only if the speed is over 55 km/h (that is, 50 plus the 5 km/h margin). Otherwise it is dropped and recorded in the inbox as `ignored` with the reason `held_limit`.

**Implausible limits.** A limit under 20 km/h is treated as implausible. No public road the fleet drives has such a limit, so the map data is wrong, for example a yard or a pedestrian zone mapped over a road. The event is dropped and recorded as `ignored` with the reason `implausible_limit`.

Anything that passes both checks stands as recorded: the limit is fresh and plausible, and the speed was over limit plus 5 km/h for 10 s.

## Worked cases

- EV-9101 on City Hauler 12: speed 58 km/h, limit 10 km/h from `here`, age 2 s. The limit is fresh but under 20 km/h, so it is implausible and the event should be dropped. (The road class limit of 60 km/h suggests the map had a bad value.)
- EV-9102: speed 53 km/h with a held limit of 40 km/h aged 95 s. The limit is stale, so it is re-judged against 50 km/h. 53 is not over 55, so the event should be dropped.
- EV-9103: speed 62 km/h, limit 50 km/h from `osm`, age 3 s. Fresh and plausible, so it stands.

## Confidence

Overspeed events carry high confidence, because the speed signal is direct. Low confidence appears on rules with a noisy signal, namely the speed-delta harsh rules and the camera-based close-following rule. Such events are counted but should be reviewed before anyone acts on them.

## How a reviewer keeps or drops an event

Doubtful overspeed events enter the review queue with `review: pending`. The fleet lead opens the event, reads the payload (`speedKph`, `limitKph`, `limitSource`, `limitAgeS`, `heldLimitKph`, `band`), applies the two checks above and chooses keep or drop. A kept event joins the driver score, a dropped event never does. Until the decision is made, the event is held out of the score. A reviewer should note the reason so that repeated map errors on the same road can be reported.
