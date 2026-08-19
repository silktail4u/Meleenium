import os
import time
import queue
import threading

from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By

app = Flask(__name__)

request_queue = queue.Queue()


def find_element_with_timeout(driver, locator, max_timeout_ms=30000):
    start_time = time.monotonic()

    while (time.monotonic() - start_time) * 1000 < max_timeout_ms:
        elements = driver.find_elements(*locator)

        if elements:
            return elements[0]

        time.sleep(0.5)

    return None


def find_matches(driver, max_timeout_ms=30000):
    base_xpath = (
        '//*[@id="main"]/div/div/div/div[2]/div/div[2]/div[2]/div/'
        'div[1]/div[2]/div[4]/div/div/div[~]'
    )

    MatchXPaths = []
    index = 2

    while True:
        xpath = base_xpath.replace("~", str(index))

        element = find_element_with_timeout(
            driver,
            (By.XPATH, xpath),
            max_timeout_ms
        )

        if element is None:
            break

        MatchXPaths.append(xpath)
        index += 2

    return MatchXPaths


def find_specific_match(
    driver,
    MatchXPaths,
    player1,
    player2,
    max_timeout_ms=30000
):
    for matchIndex, matchXPath in enumerate(MatchXPaths, start=1):

        matchSlot = matchXPath.split("[")[-1].rstrip("]")

        p1_xpath = (
            '//*[@id="main"]/div/div/div/div[2]/div/div[2]/div[2]/div/'
            'div[1]/div[2]/div[4]/div/div/div['
            f'{matchSlot}'
            ']/div/div[1]/div/div/div[1]/div'
        )

        p2_xpath = (
            '//*[@id="main"]/div/div/div/div[2]/div/div[2]/div[2]/div/'
            'div[1]/div[2]/div[4]/div/div/div['
            f'{matchSlot}'
            ']/div/div[3]/div/div/div[1]/div'
        )

        p1_element = find_element_with_timeout(
            driver,
            (By.XPATH, p1_xpath),
            max_timeout_ms
        )

        p2_element = find_element_with_timeout(
            driver,
            (By.XPATH, p2_xpath),
            max_timeout_ms
        )

        if p1_element is None or p2_element is None:
            continue

        found_p1 = p1_element.text.strip()
        found_p2 = p2_element.text.strip()

        if (
            (found_p1 == player1 and found_p2 == player2)
            or
            (found_p1 == player2 and found_p2 == player1)
        ):
            return matchIndex

    return None


def report_match(
    driver,
    matchIndex,
    p1wins,
    p2wins,
    max_timeout_ms=30000
):
    if not 0 <= p1wins <= 4:
        raise ValueError("p1wins must be between 0 and 4")

    if not 0 <= p2wins <= 4:
        raise ValueError("p2wins must be between 0 and 4")

    matchSlot = matchIndex * 2

    base_xpath = (
        '//*[@id="main"]/div/div/div/div[2]/div/div[2]/div[2]/div/'
        'div[1]/div[2]/div[4]/div/div/div['
        f'{matchSlot}'
        ']'
    )

    # Report Match
    report_xpath = f'{base_xpath}/div/div[5]/div[1]/button[3]'

    report_button = find_element_with_timeout(
        driver,
        (By.XPATH, report_xpath),
        max_timeout_ms
    )

    if report_button is None:
        raise RuntimeError("Could not find the Report Match button")

    report_button.click()

    # P1 score
    p1_score_xpath = (
        f'{base_xpath}/div/div[5]/div[3]/div/div[2]/div[2]/'
        f'span/button[{p1wins + 1}]'
    )

    p1_score_button = find_element_with_timeout(
        driver,
        (By.XPATH, p1_score_xpath),
        max_timeout_ms
    )

    if p1_score_button is None:
        raise RuntimeError("Could not find the P1 score button")

    p1_score_button.click()

    # P2 score
    p2_score_xpath = (
        f'{base_xpath}/div/div[5]/div[3]/div/div[3]/div[2]/'
        f'span/button[{p2wins + 1}]'
    )

    p2_score_button = find_element_with_timeout(
        driver,
        (By.XPATH, p2_score_xpath),
        max_timeout_ms
    )

    if p2_score_button is None:
        raise RuntimeError("Could not find the P2 score button")

    p2_score_button.click()

    # Submit
    submit_xpath = (
        f'{base_xpath}/div/div[5]/div[3]/div/div[4]/button[2]'
    )

    submit_button = find_element_with_timeout(
        driver,
        (By.XPATH, submit_xpath),
        max_timeout_ms
    )

    if submit_button is None:
        raise RuntimeError("Could not find the Submit button")

    submit_button.click()


def login(driver, max_timeout_ms=30000):
    login_url = os.environ["LOGINURL"]
    username = os.environ["USERNAME"]
    password = os.environ["PASSWORD"]

    driver.get(login_url)

    username_xpath = (
        '//*[@id="app_feature_canvas"]/div/div/div[2]/div/div/div/div[1]/'
        'form/div[2]/div[1]/div/div[2]/div/div/input'
    )

    password_xpath = (
        '//*[@id="app_feature_canvas"]/div/div/div[2]/div/div/div/div[1]/'
        'form/div[2]/div[1]/div/div[4]/div/div/input'
    )

    username_input = find_element_with_timeout(
        driver,
        (By.XPATH, username_xpath),
        max_timeout_ms
    )

    if username_input is None:
        raise RuntimeError("Could not find the username input")

    password_input = find_element_with_timeout(
        driver,
        (By.XPATH, password_xpath),
        max_timeout_ms
    )

    if password_input is None:
        raise RuntimeError("Could not find the password input")

    username_input.send_keys(username)
    password_input.send_keys(password)


def process_request(player1, player2, p1wins, p2wins):
    driver = None

    try:
        driver = webdriver.Chrome()

        # Login before doing anything else.
        login(driver)

        # Now navigate to the main page.
        url = os.environ["URL"]
        driver.get(url)

        # Find all matches ONCE.
        MatchXPaths = find_matches(driver)

        # Find the requested match from those results.
        matchIndex = find_specific_match(
            driver,
            MatchXPaths,
            player1,
            player2
        )

        if matchIndex is None:
            raise RuntimeError(
                f"Could not find a match between "
                f"{player1} and {player2}"
            )

        # Report the match.
        report_match(
            driver,
            matchIndex,
            p1wins,
            p2wins
        )

        return {
            "success": {
                "player1": player1,
                "player2": player2,
                "p1wins": p1wins,
                "p2wins": p2wins,
                "matchIndex": matchIndex
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

    finally:
        if driver is not None:
            driver.quit()


def selenium_worker():
    while True:
        job = request_queue.get()

        try:
            result, status = process_request(
                job["player1"],
                job["player2"],
                job["p1wins"],
                job["p2wins"]
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
    daemon=True
)

worker_thread.start()


@app.route("/find-matches", methods=["POST"])
def find_matches_endpoint():
    try:
        data = request.get_json()

        player1 = data["player1"]
        player2 = data["player2"]
        p1wins = int(data["p1wins"])
        p2wins = int(data["p2wins"])

        job = {
            "player1": player1,
            "player2": player2,
            "p1wins": p1wins,
            "p2wins": p2wins,
            "event": threading.Event(),
            "result": None,
            "status": None
        }

        request_queue.put(job)

        job["event"].wait()

        return jsonify(job["result"]), job["status"]

    except Exception as e:
        return jsonify({
            "success": False,
            "response": {
                "status": 500,
                "error": str(e)
            }
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        threaded=True
    )
