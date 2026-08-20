import os
import queue
import threading
from urllib.parse import urlparse

import requests
from flask import Flask, request, jsonify


app = Flask(__name__)

request_queue = queue.Queue()

# ============================================================
# Configuration
# ============================================================

STARTGG_API_URL = "https://api.start.gg/gql/alpha"

# Required:
#   STARTGG_BEARER=your_bearer_token
STARTGG_BEARER = os.environ["STARTGG_BEARER"]

# Event configuration.
#
# Preferred:
#   STARTGG_EVENT_SLUG=my-tournament/singles
#
# Alternatives:
#   STARTGG_EVENT_ID=123456
#   URL=https://www.start.gg/tournament/my-tournament/event/singles
#
# Priority:
#   1. STARTGG_EVENT_ID
#   2. STARTGG_EVENT_SLUG
#   3. URL

STARTGG_EVENT_ID = os.environ.get("STARTGG_EVENT_ID")
STARTGG_EVENT_SLUG = os.environ.get("STARTGG_EVENT_SLUG")
STARTGG_URL = os.environ.get("URL")


# ============================================================
# start.gg GraphQL helper
# ============================================================

def startgg_request(query, variables=None):
    """
    Execute a GraphQL request against the start.gg API.
    """

    headers = {
        "Authorization": f"Bearer {STARTGG_BEARER}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "query": query,
        "variables": variables or {},
    }

    response = requests.post(
        STARTGG_API_URL,
        headers=headers,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if "errors" in result:
        messages = []

        for error in result["errors"]:
            messages.append(
                error.get("message", str(error))
            )

        raise RuntimeError(
            "start.gg API error: " + "; ".join(messages)
        )

    return result["data"]


# ============================================================
# Event lookup
# ============================================================

def get_event_id_from_slug(slug):
    """
    Resolve a start.gg event slug into its numeric event ID.

    Example:

        my-tournament/singles
    """

    query = """
    query GetEvent($slug: String!) {
        event(slug: $slug) {
            id
            name
        }
    }
    """

    data = startgg_request(
        query,
        {
            "slug": slug
        }
    )

    event = data.get("event")

    if not event:
        raise RuntimeError(
            f"Could not find start.gg event: {slug}"
        )

    return str(event["id"])


def extract_event_id():
    """
    Determine the event ID.

    Priority:

    1. STARTGG_EVENT_ID
    2. STARTGG_EVENT_SLUG
    3. URL
    """

    # --------------------------------------------------------
    # Option 1: Explicit event ID
    # --------------------------------------------------------

    if STARTGG_EVENT_ID:
        return str(STARTGG_EVENT_ID)

    # --------------------------------------------------------
    # Option 2: Event slug
    # --------------------------------------------------------

    if STARTGG_EVENT_SLUG:
        return get_event_id_from_slug(
            STARTGG_EVENT_SLUG
        )

    # --------------------------------------------------------
    # Option 3: Existing URL
    # --------------------------------------------------------

    if STARTGG_URL:

        parsed = urlparse(STARTGG_URL)

        path_parts = [
            part
            for part in parsed.path.split("/")
            if part
        ]

        # Expected:
        #
        # /tournament/<tournament-slug>/event/<event-slug>
        #

        if "event" not in path_parts:
            raise RuntimeError(
                "URL must be a start.gg event URL "
                "containing /event/"
            )

        event_index = path_parts.index("event")

        if event_index + 1 >= len(path_parts):
            raise RuntimeError(
                "Could not determine event slug from URL"
            )

        if len(path_parts) < 2:
            raise RuntimeError(
                "Could not determine tournament slug from URL"
            )

        tournament_slug = path_parts[1]
        event_slug = path_parts[event_index + 1]

        slug = f"{tournament_slug}/{event_slug}"

        return get_event_id_from_slug(slug)

    # --------------------------------------------------------
    # Nothing configured
    # --------------------------------------------------------

    raise RuntimeError(
        "Set one of STARTGG_EVENT_ID, "
        "STARTGG_EVENT_SLUG, or URL"
    )


# ============================================================
# Get sets
# ============================================================

def get_event_sets(event_id):
    """
    Retrieve all sets from the configured start.gg event.

    start.gg paginates sets, so this keeps requesting pages
    until all sets have been retrieved.
    """

    all_sets = []

    page = 1
    per_page = 100

    query = """
    query EventSets(
        $eventId: ID!,
        $page: Int!,
        $perPage: Int!
    ) {
        event(id: $eventId) {
            id
            name

            sets(
                page: $page
                perPage: $perPage
                sortType: STANDARD
            ) {
                pageInfo {
                    total
                }

                nodes {
                    id
                    state

                    slots {
                        id

                        entrant {
                            id
                            name
                        }

                        standing {
                            stats {
                                score {
                                    value
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    """

    while True:

        data = startgg_request(
            query,
            {
                "eventId": event_id,
                "page": page,
                "perPage": per_page,
            }
        )

        event = data.get("event")

        if not event:
            raise RuntimeError(
                f"Could not find event {event_id}"
            )

        sets = event["sets"]

        nodes = sets["nodes"]

        all_sets.extend(nodes)

        total = sets["pageInfo"]["total"]

        if len(all_sets) >= total:
            break

        page += 1

    return all_sets


# ============================================================
# Find requested match
# ============================================================

def find_specific_match(
    sets,
    player1,
    player2
):
    """
    Find a set between player1 and player2.

    Player order does not matter.
    """

    player1 = player1.strip()
    player2 = player2.strip()

    for match_index, match in enumerate(
        sets,
        start=1
    ):

        slots = match.get("slots", [])

        if len(slots) < 2:
            continue

        entrants = []

        for slot in slots:

            entrant = slot.get("entrant")

            if entrant:

                entrants.append({
                    "id": str(entrant["id"]),
                    "name": entrant["name"],
                    "slot": slot,
                })

        # A normal 1v1 set should have exactly two entrants.

        if len(entrants) != 2:
            continue

        found_p1 = entrants[0]["name"].strip()
        found_p2 = entrants[1]["name"].strip()

        if (
            (
                found_p1 == player1
                and
                found_p2 == player2
            )
            or
            (
                found_p1 == player2
                and
                found_p2 == player1
            )
        ):

            return {
                "matchIndex": match_index,
                "set": match,
                "entrant1": entrants[0],
                "entrant2": entrants[1],
            }

    return None


# ============================================================
# Report match
# ============================================================

def report_match(
    match,
    player1,
    player2,
    p1wins,
    p2wins
):
    """
    Report the winner of a set through start.gg.

    p1wins/p2wins are validated as scores from 0 through 4.

    The reportBracketSet mutation requires the set ID and
    winner ID.
    """

    # --------------------------------------------------------
    # Validate scores
    # --------------------------------------------------------

    if not 0 <= p1wins <= 4:
        raise ValueError(
            "p1wins must be between 0 and 4"
        )

    if not 0 <= p2wins <= 4:
        raise ValueError(
            "p2wins must be between 0 and 4"
        )

    if p1wins == p2wins:
        raise ValueError(
            "The match score cannot be tied"
        )

    # --------------------------------------------------------
    # Get IDs
    # --------------------------------------------------------

    set_id = str(
        match["set"]["id"]
    )

    entrant1_id = str(
        match["entrant1"]["id"]
    )

    entrant2_id = str(
        match["entrant2"]["id"]
    )

    # --------------------------------------------------------
    # Determine which entrant is player1
    # --------------------------------------------------------

    if (
        match["entrant1"]["name"].strip()
        ==
        player1.strip()
    ):

        player1_entrant_id = entrant1_id
        player2_entrant_id = entrant2_id

    else:

        player1_entrant_id = entrant2_id
        player2_entrant_id = entrant1_id

    # --------------------------------------------------------
    # Determine winner
    # --------------------------------------------------------

    if p1wins > p2wins:
        winner_id = player1_entrant_id
    else:
        winner_id = player2_entrant_id

    # --------------------------------------------------------
    # Report set
    # --------------------------------------------------------

    mutation = """
    mutation ReportSet(
        $setId: ID!,
        $winnerId: ID!
    ) {
        reportBracketSet(
            setId: $setId,
            winnerId: $winnerId
        ) {
            id
            state
        }
    }
    """

    data = startgg_request(
        mutation,
        {
            "setId": set_id,
            "winnerId": winner_id,
        }
    )

    return {
        "setId": set_id,
        "winnerId": winner_id,
        "p1wins": p1wins,
        "p2wins": p2wins,
        "apiResult": data,
    }


# ============================================================
# Process request
# ============================================================

def process_request(
    player1,
    player2,
    p1wins,
    p2wins
):
    try:

        # ----------------------------------------------------
        # Determine event
        # ----------------------------------------------------

        event_id = extract_event_id()

        # ----------------------------------------------------
        # Get all sets
        # ----------------------------------------------------

        sets = get_event_sets(
            event_id
        )

        # ----------------------------------------------------
        # Find requested matchup
        # ----------------------------------------------------

        match = find_specific_match(
            sets,
            player1,
            player2,
        )

        if match is None:

            raise RuntimeError(
                f"Could not find a match between "
                f"{player1} and {player2}"
            )

        # ----------------------------------------------------
        # Report match
        # ----------------------------------------------------

        report_result = report_match(
            match,
            player1,
            player2,
            p1wins,
            p2wins,
        )

        # ----------------------------------------------------
        # Success response
        # ----------------------------------------------------

        return {
            "success": {
                "player1": player1,
                "player2": player2,
                "p1wins": p1wins,
                "p2wins": p2wins,
                "matchIndex": match["matchIndex"],
                "setId": report_result["setId"],
            },
            "response": {
                "status": 200
            }
        }, 200

    except Exception as e:

        return {
            "success": False,
            "response": {
                "status": 500,
                "error": str(e)
            }
        }, 500


# ============================================================
# API worker
# ============================================================

def selenium_worker():
    """
    Process queued requests one at a time.

    The function keeps the original name so the overall
    structure of the original application remains familiar.

    There is no Selenium involved anymore.
    """

    while True:

        job = request_queue.get()

        try:

            result, status = process_request(
                job["player1"],
                job["player2"],
                job["p1wins"],
                job["p2wins"],
            )

            job["result"] = result
            job["status"] = status

        except Exception as e:

            job["result"] = {
                "success": False,
                "response": {
                    "status": 500,
                    "error": str(e)
                }
            }

            job["status"] = 500

        finally:

            job["event"].set()

            request_queue.task_done()


worker_thread = threading.Thread(
    target=selenium_worker,
    daemon=True,
)

worker_thread.start()


# ============================================================
# Flask endpoint
# ============================================================

@app.route(
    "/find-matches",
    methods=["POST"]
)
def find_matches_endpoint():

    try:

        data = request.get_json()

        if not data:

            raise ValueError(
                "Request body must contain JSON"
            )

        # ----------------------------------------------------
        # Get request parameters
        # ----------------------------------------------------

        player1 = data["player1"]
        player2 = data["player2"]

        p1wins = int(
            data["p1wins"]
        )

        p2wins = int(
            data["p2wins"]
        )

        # ----------------------------------------------------
        # Create queued job
        # ----------------------------------------------------

        job = {
            "player1": player1,
            "player2": player2,
            "p1wins": p1wins,
            "p2wins": p2wins,
            "event": threading.Event(),
            "result": None,
            "status": None,
        }

        request_queue.put(job)

        # ----------------------------------------------------
        # Wait for worker
        # ----------------------------------------------------

        job["event"].wait()

        # ----------------------------------------------------
        # Return result
        # ----------------------------------------------------

        return jsonify(
            job["result"]
        ), job["status"]

    except Exception as e:

        return jsonify({
            "success": False,
            "response": {
                "status": 500,
                "error": str(e)
            }
        }), 500


# ============================================================
# Start Flask
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        threaded=True,
    )


