# Driver Score Formula

The driver score is a single number from 0 to 100 that summarises how a vehicle was driven over a period. It is always labelled **provisional**, because events can still be reviewed and the underlying data can still change. This page gives the exact formula so that any score can be reproduced by hand.

## Event weights

Each event adds to a penalty. The weights are:

- `overspeed`: 5 multiplied by the band (band 1 to 4).
- `harsh_brake`: 4, or 8 when the event is severe.
- `harsh_accel`: 3.
- `wot`: 1.
- `over_rev`: 1.
- `check_engine`: 0.
- `close_following`: 0.

Check-engine and close-following events are recorded and visible, but they do not reduce the score. Close following is a beta rule with low confidence, and a lit check-engine lamp is a vehicle problem, not a driving habit.

## The formula

score = clamp(round(100 - penalty x 10 / max(distanceKm, 10)), 0, 100)

The penalty is the sum of all event weights in the period. It is scaled per distance, so a long trip with a few events scores better than a short trip with the same events. The `max(distanceKm, 10)` part is the **10 km floor**: a trip shorter than 10 km is treated as if it were 10 km long, which stops a single event on a 3 km errand from collapsing the score. The result is rounded to the nearest whole number and kept between 0 and 100.

## Minimum distance

A score needs at least 2 km of driving. Under 2 km there is no score, and the summary reports `scoreReason: too_short` with an empty score. Route Van 8 (KW-0008) with its 1.6 km trip is an example.

## Bands

The number maps to a band:

- **excellent**: 90 and up
- **good**: 75 and up
- **needs attention**: 60 and up
- **poor**: below 60

## Pending reviews are held out

An event whose review is still pending is not counted in the penalty. Overspeed events in particular can be doubtful when the speed limit data was held or implausible, so the fleet lead keeps or drops them first. Until then the score may be higher than it will finally be, which is one more reason it is provisional. The summary also notes when part of the data was not analysed, for example when event deliveries were failing.

## Worked example

City Hauler 12 (KW-0012) drove 38.4 km over the week and recorded seven overspeed events that counted: three in band 4, two in band 3 and two in band 2.

- Band 4: 3 events x 5 x 4 = 60
- Band 3: 2 events x 5 x 3 = 30
- Band 2: 2 events x 5 x 2 = 20

The penalty is 110. The distance, 38.4 km, is over the 10 km floor, so it is used as is. The score is 100 - 110 x 10 / 38.4, which is 100 - 28.6, rounded to 71. That falls in the **needs attention** band. Two further overspeed events (EV-9101 and EV-9102) are held out because their review is pending; if they were kept, the score would drop.

## Reading counts per 100 km

The summary also gives each event kind per 100 km, which makes vehicles with very different mileage comparable. City Hauler 12's seven overspeed events over 38.4 km are 18.2 per 100 km. Always quote the score together with its band and say it is provisional.
