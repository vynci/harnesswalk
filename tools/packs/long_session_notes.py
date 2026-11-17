# ruff: noqa: E501  (the note briefs are data, one per line)
"""Generate the long_session pack's pasted planning notes with the hosted generator (Haiku).

    ANTHROPIC_API_KEY=... uv run python tools/packs/long_session_notes.py [OUT_DIR]
    MORE=1 ...   # the second batch (three more notes per case)

The notes are the long user turns of the pack's compaction cases (golden.jsonl). Usage is
recorded in tools/packs/long_session.usage.jsonl like any gen_pack stage, so the provenance
card counts it. Output: OUT_DIR/<id>.md (default ./notes). The key is read from the
environment only, as in tools/gen_pack.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("notes")
spec = importlib.util.spec_from_file_location("gen_pack", ROOT / "tools" / "gen_pack.py")
gp = importlib.util.module_from_spec(spec)
sys.modules["gen_pack"] = gp
spec.loader.exec_module(gp)  # type: ignore[union-attr]

MORE = {
    "lc1": [
        (
            "lc1_n6",
            "A few thoughts about the inn and the evenings.",
            "What they hope the Carrow Harbour Inn is like, reading by a window, evening walks to the harbour lights.",
        ),
        (
            "lc1_n7",
            "My sister sent me her list of birds she wants to see.",
            "Invented sea birds and waders, where they might be, a pair of binoculars to share.",
        ),
        (
            "lc1_n8",
            "And some worries, written down so I stop thinking about them.",
            "Weather, a rough crossing, seasickness tips, what to do if a walk is closed.",
        ),
    ],
    "lc2": [
        (
            "lc2_n6",
            "Notes on restaurants we might try in Mirelake.",
            "Invented places, asking about nut-free kitchens, a quiet table, a lake view.",
        ),
        (
            "lc2_n7",
            "Some things my partner wants to see.",
            "A clock tower, an old boatyard, a little gallery, sunset over the lake.",
        ),
        (
            "lc2_n8",
            "A short plan for each of the three days.",
            "Morning, afternoon and evening ideas, kept loose, with rest time.",
        ),
    ],
    "lc3": [
        (
            "lc3_n6",
            "Bedtime and sleep on the island, my biggest worry.",
            "Routines, blackout, a night light, early dinners, quiet evenings.",
        ),
        (
            "lc3_n7",
            "What to pack for the children.",
            "Layers, wellies, spare socks, toys for the ferry, a first aid pouch.",
        ),
        (
            "lc3_n8",
            "Ideas for the grandparents' presents.",
            "Shells in a jar, drawings, a small island print, a postcard each day.",
        ),
    ],
    "lc4": [
        (
            "lc4_n8",
            "About my mother's walking and resting.",
            "Short distances, benches, a stick, warm places to sit, no steep paths.",
        ),
        (
            "lc4_n9",
            "Thoughts about the guesthouse room.",
            "A sea view, a ground floor room for the mother, a quiet corner, breakfast times.",
        ),
        (
            "lc4_n10",
            "One last set of notes about the journey home.",
            "Leaving on the final day, luggage, a last walk, a lunch before the train.",
        ),
    ],
}

NOTES = {
    "lc1": [
        (
            "lc1_n1",
            "Booking reference TR-4471: two adults, the Isle of Carrow, 14 to 19 November 2026, and here are my notes from planning it with my sister.",
            "Why the trip, what the two sisters like (sea birds, quiet coves, slow mornings), loose ideas for each day. In the second half of the note, in the middle of a paragraph, include exactly this sentence: The key safe code for the walkers' hut is 7302.",
        ),
        (
            "lc1_n2",
            "Next, the walks we cut out of an old walking magazine.",
            "Five invented coastal walks on the Isle of Carrow with distances, rough terrain notes and which one to do in rain.",
        ),
        (
            "lc1_n3",
            "Now food, because my sister is vegetarian and I eat anything from the sea.",
            "Invented cafes and a harbour fish stall, what each sister wants to try, a picnic plan.",
        ),
        (
            "lc1_n4",
            "Here is my camera kit list and the shots I am hoping for.",
            "Lenses, a small tripod, batteries, the light at dawn on the cliffs, sea birds, keeping gear dry.",
        ),
        (
            "lc1_n5",
            "Last set of notes: the money side of this trip.",
            "A rough budget: ferry foot fares, the inn, meals, a boat trip, souvenirs, a buffer; no exact totals needed beyond simple sums.",
        ),
    ],
    "lc2": [
        (
            "lc2_n1",
            "Trip TR-4520 for my partner and me: my partner has a severe nut allergy, so every breakfast and every meal out must be nut free.",
            "A short Mirelake break; what they want from it; how they handle the allergy when travelling (carrying medication, telling staff early).",
        ),
        (
            "lc2_n2",
            "Some ideas for walks around the lake that we found.",
            "Four invented lakeside walks with distances and viewpoints, a boathouse cafe, winter light.",
        ),
        (
            "lc2_n3",
            "Here is our wish list for the Mirelake winter market.",
            "Crafts, a woodturner, warm drinks, gifts for family, a budget for the market.",
        ),
        (
            "lc2_n4",
            "Thoughts about the train down from Brannock.",
            "Seats, luggage, a flask of tea, scenery on the line, what to do if the train is late.",
        ),
        (
            "lc2_n5",
            "Finally, my packing list for a cold lakeside weekend.",
            "Layers, boots, a rain shell, the allergy kit, chargers, a book each.",
        ),
    ],
    "lc3": [
        (
            "lc3_n1",
            "Decision for trip TR-4588: day crossings only, we will not take the overnight ferry because the children are 6 and 9.",
            "A family of four going to the Isle of Carrow from 2 to 6 December 2026; the children's routines; why sleep matters; what the parents hope for.",
        ),
        (
            "lc3_n2",
            "Things the kids want to do on the island.",
            "Rock pools, the harbour, a lighthouse, a playground, a beach walk, collecting shells.",
        ),
        (
            "lc3_n3",
            "Food notes for two fussy eaters.",
            "What each child eats, snacks for the ferry, places with simple meals, a birthday cake idea.",
        ),
        (
            "lc3_n4",
            "Our rainy-day plans, because December can be wet.",
            "Indoor ideas, card games, a small museum, a cafe with a play corner, drying wet clothes.",
        ),
        (
            "lc3_n5",
            "Last notes: we will travel as foot passengers, not with the car.",
            "Why no car, how to carry luggage with children, the harbour bus, a folding pushchair.",
        ),
    ],
    "lc4": [
        (
            "lc4_n1",
            "Change for trip TR-4602: we are now three travellers, my mother is coming with us.",
            "Why the mother joins this anniversary trip to Port Ellery; her needs (a ground floor room if possible, short walks); excitement about the sea.",
        ),
        (
            "lc4_n2",
            "Second change for TR-4602: we arrive on 2026-12-10, not 2026-12-09, because of my work.",
            "The work reason, what this means for the first evening, keeping the anniversary dinner on the right night.",
        ),
        (
            "lc4_n3",
            "Ideas for the anniversary dinner.",
            "Invented seafood restaurants, a table by the window, a surprise for the partner, a dessert.",
        ),
        (
            "lc4_n4",
            "Winter beach walks we would like to do.",
            "Three invented beaches and coves, tides, wind, a warm drink afterwards, a walk the mother can manage.",
        ),
        (
            "lc4_n5",
            "Notes on the train from Mirelake.",
            "Departure times are not settled; luggage for three; seats together; a picnic on board.",
        ),
        (
            "lc4_n6",
            "Gift ideas from Port Ellery.",
            "Local crafts, sea glass, a scarf for the mother, postcards, a small budget.",
        ),
        (
            "lc4_n7",
            "Maybe a day trip to the Isle of Carrow on the ferry from Port Ellery.",
            "Pros and cons, the long winter crossing, which day of the week it could be, what to see in a few hours.",
        ),
    ],
}

COMMON = """You write synthetic, fictional travel planning notes that a traveller pastes into a chat
with a travel agency's assistant. Everything is invented: no real places, companies, brands or
people; no phone numbers, emails, web addresses or street addresses. Never use the em-dash
character. Write plain, warm, first-person prose with short paragraphs and an occasional list.

Write each note as a block, and nothing outside the blocks:

=== FILE: notes/<id>.md ===
...the note...
=== END FILE ===

Each note is 560 to 640 words. Its first sentence is exactly the sentence given, word for word.
"""


def main() -> None:
    gp.use_model("haiku")
    OUT.mkdir(exist_ok=True)
    import os

    batch = MORE if os.environ.get("MORE") else NOTES
    for case, notes in batch.items():
        listing = "\n".join(
            f'- id `{i}`. First sentence: "{first}" Content: {what}' for i, first, what in notes
        )
        user = (
            "Destinations (all invented): the Isle of Carrow (ferry from Mirelake harbour, 1 h 40 min;"
            " in winter also from Port Ellery on Friday to Monday), Mirelake (a lakeside town with a"
            " rail station), the Brannock Highlands and Port Ellery (a coastal town).\n\n"
            f"Write these notes now:\n{listing}"
        )
        text, usage = gp.call_model(COMMON, user, 12000)
        gp.record_usage("long_session", f"notes[{case}{'+' if batch is MORE else ''}]", usage)
        files = gp.parse_files(text)
        for rel, body in files.items():
            (OUT / Path(rel).name).write_text(body, encoding="utf-8")
        print(case, sorted(files))


if __name__ == "__main__":
    main()
