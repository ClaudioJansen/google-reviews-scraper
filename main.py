import re
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote_plus

import pandas as pd
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

ESTABLISHMENT_NAME = "Disney's Hollywood Studios"
TARGET_STARS = 4
MAX_REVIEWS = 5
OUTPUT_FOLDER = "./output"
HEADLESS = False
WAIT_TIMEOUT = 30000

MAPS_LANGUAGE_CODE = "pt-BR"
GOOGLE_MAPS_URL = f"https://www.google.com/maps?hl={MAPS_LANGUAGE_CODE}"
BROWSER_LOCALE = "pt-BR"
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
POST_ACTION_WAIT_MS = 1500
SCROLL_WAIT_MS = 2000
MAX_IDLE_SCROLLS = 10
DATE_NOT_INFORMED = "Não informado"
SEARCH_LOAD_WAIT_MS = 10000
POLL_INTERVAL_MS = 300
MAX_REVIEW_BUTTON_CANDIDATES = 5
REVIEWS_READY_WAIT_MULTIPLIER = 4

SEARCH_BOX_SELECTORS = (
    "input#searchboxinput",
    "input[name='q']",
    "input[role='combobox'][name='q']",
    "input[aria-label*='Search Google Maps']",
    "input[aria-label*='Pesquisar no Google Maps']",
)
FIRST_RESULT_SELECTOR = "a.hfpxzc"
REVIEWS_CONTAINER_SELECTORS = (
    "div[role='feed']",
    "div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde",
    "div.m6QErb.DxyBCb.kA9KIf.dS8AEf",
)
REVIEW_CARD_SELECTOR = "div.jftiEf"

REVIEWS_BUTTON_SELECTORS = (
    "button[jsaction*='pane.rating.moreReviews']",
    "button[jsaction*='reviewChart.moreReviews']",
    "button[aria-label*='Reviews for']",
    "button[aria-label*='Avalia']",
    "button:has-text('reviews')",
    "button:has-text('Reviews')",
    "button:has-text('avaliações')",
    "button:has-text('Avali')",
)

SORT_BUTTON_SELECTORS = (
    "button[aria-label*='Sort reviews']",
    "button[aria-label*='Classificar avaliações']",
    "button[aria-label*='Ordenar avaliações']",
)

NEWEST_OPTIONS = ("Newest", "Most recent", "Mais recentes")

REVIEWER_NAME_SELECTORS = (".d4r55", ".TSUbDb")
REVIEW_TEXT_SELECTORS = (".wiI7pd", ".MyEned")
REVIEW_DATE_SELECTORS = (".rsqaWe", "span:has-text('ago')", "span:has-text('há')")
RATING_LABEL_SELECTORS = ("span.kvMYJc", "span[aria-label*='star']", "span[aria-label*='estrela']")
EXPAND_REVIEW_BUTTON_SELECTORS = ("button.w8nwRe", "button:has-text('More')", "button:has-text('Mais')")


def log_info(message: str) -> None:
    print(f"[INFO] {message}")


def log_warning(message: str) -> None:
    print(f"[WARNING] {message}")


def log_error(message: str) -> None:
    print(f"[ERROR] {message}")


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def sanitize_filename(value: str) -> str:
    safe_value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    safe_value = re.sub(r"[\s-]+", "_", safe_value, flags=re.UNICODE).strip("_")
    return safe_value or "output"


def parse_stars_from_label(label: str) -> int:
    match = re.search(r"(\d+)", label)
    if not match:
        return 0
    return int(match.group(1))


def run_with_retry(step_name: str, action: Callable[[], None]) -> None:
    last_exception: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            action()
            return
        except PlaywrightTimeoutError as exc:
            last_exception = exc
            log_warning(f"{step_name} timed out (attempt {attempt}/{RETRY_ATTEMPTS}).")
        except Exception as exc:  # noqa: BLE001
            last_exception = exc
            log_warning(f"{step_name} failed (attempt {attempt}/{RETRY_ATTEMPTS}): {exc}")
        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_DELAY_SECONDS)
    if last_exception:
        raise last_exception


def first_visible_locator(page: Page, selectors: tuple[str, ...]) -> Optional[Locator]:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible():
                return locator
        except Exception:  # noqa: BLE001
            continue
    return None


def wait_for_first_visible_locator(page: Page, selectors: tuple[str, ...], timeout_ms: int) -> Locator:
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        locator = first_visible_locator(page, selectors)
        if locator:
            return locator
        page.wait_for_timeout(POLL_INTERVAL_MS)
    raise PlaywrightTimeoutError(f"Could not find visible locator among selectors: {selectors}")


def wait_for_reviews_container(page: Page) -> Locator:
    return wait_for_first_visible_locator(page, REVIEWS_CONTAINER_SELECTORS, WAIT_TIMEOUT)


def is_reviews_section_ready(page: Page) -> bool:
    try:
        has_card = page.locator(REVIEW_CARD_SELECTOR).count() > 0
        if not has_card:
            return False
        has_container = first_visible_locator(page, REVIEWS_CONTAINER_SELECTORS) is not None
        has_sort_button = first_visible_locator(page, SORT_BUTTON_SELECTORS) is not None
        return has_container or has_sort_button
    except Exception:  # noqa: BLE001
        return False


def wait_for_reviews_ready(page: Page, timeout_ms: int) -> None:
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        if is_reviews_section_ready(page):
            return
        page.wait_for_timeout(POLL_INTERVAL_MS)
    raise PlaywrightTimeoutError("Reviews section did not become ready.")


def is_search_successful(page: Page) -> bool:
    try:
        if "/place/" in page.url:
            return True
        if first_visible_locator(page, REVIEWS_BUTTON_SELECTORS):
            return True
        if page.locator(REVIEW_CARD_SELECTOR).count() > 0:
            return True
        if page.locator(FIRST_RESULT_SELECTOR).count() > 0:
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def wait_for_search_success(page: Page, timeout_ms: int) -> None:
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        if is_search_successful(page):
            return
        page.wait_for_timeout(POLL_INTERVAL_MS)
    raise PlaywrightTimeoutError("Search did not load establishment result.")


def build_maps_search_url(establishment_name: str) -> str:
    encoded_name = quote_plus(establishment_name)
    return f"https://www.google.com/maps/search/?api=1&query={encoded_name}&hl={MAPS_LANGUAGE_CODE}"


def get_text_from_selectors(parent: Locator, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        locator = parent.locator(selector).first
        try:
            if locator.count() > 0:
                text = normalize_text(locator.inner_text())
                if text:
                    return text
        except Exception:  # noqa: BLE001
            continue
    return ""


def get_attribute_from_selectors(parent: Locator, selectors: tuple[str, ...], attribute: str) -> str:
    for selector in selectors:
        locator = parent.locator(selector).first
        try:
            if locator.count() > 0:
                attribute_value = normalize_text(locator.get_attribute(attribute))
                if attribute_value:
                    return attribute_value
        except Exception:  # noqa: BLE001
            continue
    return ""


def dismiss_cookies_if_needed(page: Page) -> None:
    cookie_buttons = (
        "button:has-text('Reject all')",
        "button:has-text('Recusar tudo')",
        "button:has-text('Accept all')",
        "button:has-text('Aceitar tudo')",
    )
    button = first_visible_locator(page, cookie_buttons)
    if button:
        try:
            button.click(timeout=WAIT_TIMEOUT)
            page.wait_for_timeout(POST_ACTION_WAIT_MS)
        except Exception:  # noqa: BLE001
            pass


def search_establishment(page: Page) -> None:
    log_info("Searching establishment...")

    def action() -> None:
        page.goto(GOOGLE_MAPS_URL, wait_until="domcontentloaded", timeout=WAIT_TIMEOUT)
        dismiss_cookies_if_needed(page)
        search_box = wait_for_first_visible_locator(page, SEARCH_BOX_SELECTORS, WAIT_TIMEOUT)
        search_box.fill(ESTABLISHMENT_NAME)
        search_box.press("Enter")
        page.wait_for_timeout(SEARCH_LOAD_WAIT_MS)

        try:
            wait_for_search_success(page, WAIT_TIMEOUT)
        except PlaywrightTimeoutError:
            # Fallback for dynamic UI variants where Enter does not trigger the expected state.
            page.goto(build_maps_search_url(ESTABLISHMENT_NAME), wait_until="domcontentloaded", timeout=WAIT_TIMEOUT)
            page.wait_for_timeout(SEARCH_LOAD_WAIT_MS)
            wait_for_search_success(page, WAIT_TIMEOUT)

        first_result = page.locator(FIRST_RESULT_SELECTOR).first
        if first_result.count() > 0 and first_result.is_visible():
            first_result.click(timeout=WAIT_TIMEOUT)
            page.wait_for_timeout(POST_ACTION_WAIT_MS)
            wait_for_search_success(page, WAIT_TIMEOUT)

    run_with_retry("Search establishment", action)


def open_reviews(page: Page) -> None:
    log_info("Opening reviews...")

    def action() -> None:
        if is_reviews_section_ready(page):
            return

        for selector in REVIEWS_BUTTON_SELECTORS:
            buttons = page.locator(selector)
            button_count = min(buttons.count(), MAX_REVIEW_BUTTON_CANDIDATES)

            for index in range(button_count):
                button = buttons.nth(index)
                try:
                    if not button.is_visible():
                        continue
                    button.click(timeout=WAIT_TIMEOUT)
                    page.wait_for_timeout(POST_ACTION_WAIT_MS)
                    wait_for_reviews_ready(page, POST_ACTION_WAIT_MS * REVIEWS_READY_WAIT_MULTIPLIER)
                    return
                except Exception:  # noqa: BLE001
                    continue

        wait_for_reviews_ready(page, WAIT_TIMEOUT)

    run_with_retry("Open reviews", action)


def sort_by_latest(page: Page) -> None:
    log_info("Sorting reviews by latest...")

    def action() -> None:
        sort_button = first_visible_locator(page, SORT_BUTTON_SELECTORS)
        if not sort_button:
            raise PlaywrightTimeoutError("Could not find sort button.")
        sort_button.click(timeout=WAIT_TIMEOUT)
        page.wait_for_timeout(POST_ACTION_WAIT_MS)

        for option_text in NEWEST_OPTIONS:
            option = page.locator(f"div[role='menuitemradio']:has-text('{option_text}')").first
            if option.is_visible():
                option.click(timeout=WAIT_TIMEOUT)
                page.wait_for_timeout(POST_ACTION_WAIT_MS)
                return
        raise PlaywrightTimeoutError("Could not find newest option.")

    run_with_retry("Sort by latest", action)


def filter_by_stars(page: Page) -> None:
    log_info(f"Filtering {TARGET_STARS}-star reviews...")

    def action() -> None:
        star_selectors = (
            f"button[aria-label*='{TARGET_STARS} star']",
            f"button[aria-label*='{TARGET_STARS} stars']",
            f"button[aria-label*='{TARGET_STARS} estrela']",
            f"button[aria-label*='{TARGET_STARS} estrelas']",
        )
        star_button = first_visible_locator(page, star_selectors)
        if star_button:
            star_button.click(timeout=WAIT_TIMEOUT)
            page.wait_for_timeout(POST_ACTION_WAIT_MS)
            return
        raise PlaywrightTimeoutError("Could not find star filter button.")

    try:
        run_with_retry("Filter by stars", action)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"UI star filter unavailable. Using extraction filter only. Details: {exc}")


def extract_review(review_card: Locator) -> Optional[dict]:
    for selector in EXPAND_REVIEW_BUTTON_SELECTORS:
        try:
            expand_button = review_card.locator(selector).first
            if expand_button.is_visible():
                expand_button.click(timeout=WAIT_TIMEOUT)
                break
        except Exception:  # noqa: BLE001
            continue

    reviewer_name = get_text_from_selectors(review_card, REVIEWER_NAME_SELECTORS)
    if not reviewer_name:
        return None

    rating_label = get_attribute_from_selectors(review_card, RATING_LABEL_SELECTORS, "aria-label")
    stars = parse_stars_from_label(rating_label)

    review_text = get_text_from_selectors(review_card, REVIEW_TEXT_SELECTORS)
    if not normalize_text(review_text):
        return None

    review_date = get_text_from_selectors(review_card, REVIEW_DATE_SELECTORS) or DATE_NOT_INFORMED

    return {
        "estrelas": stars,
        "nome_da_pessoa": reviewer_name,
        "avaliacao": review_text,
        "data_avaliacao": review_date,
    }


def scroll_until_n_reviews(page: Page) -> list[dict]:
    log_info("Collecting reviews...")
    reviews_feed = wait_for_reviews_container(page)

    collected_reviews: list[dict] = []
    seen_reviews: set[str] = set()
    idle_scrolls = 0
    scanned_count = 0

    while len(collected_reviews) < MAX_REVIEWS and idle_scrolls < MAX_IDLE_SCROLLS:
        review_cards = page.locator(REVIEW_CARD_SELECTOR)
        review_count = review_cards.count()
        previous_count = len(collected_reviews)
        previous_review_count = review_count

        for index in range(scanned_count, review_count):
            review_card = review_cards.nth(index)
            review_data = extract_review(review_card)
            if not review_data:
                continue

            if review_data["estrelas"] != TARGET_STARS:
                continue

            review_key = "|".join(
                [
                    review_data["nome_da_pessoa"],
                    review_data["data_avaliacao"],
                    review_data["avaliacao"],
                ]
            )
            if review_key in seen_reviews:
                continue

            collected_reviews.append(review_data)
            seen_reviews.add(review_key)
            log_info(f"Collected {len(collected_reviews)}/{MAX_REVIEWS} reviews")

            if len(collected_reviews) >= MAX_REVIEWS:
                break

        scanned_count = review_count

        reviews_feed.evaluate("(element) => { element.scrollTop = element.scrollHeight; }")
        page.wait_for_timeout(SCROLL_WAIT_MS)
        new_review_count = page.locator(REVIEW_CARD_SELECTOR).count()

        if len(collected_reviews) == previous_count and new_review_count <= previous_review_count:
            idle_scrolls += 1
        else:
            idle_scrolls = 0

    return collected_reviews[:MAX_REVIEWS]


def save_excel(reviews: list[dict]) -> Path:
    output_path = Path(OUTPUT_FOLDER)
    output_path.mkdir(parents=True, exist_ok=True)

    sanitized_name = sanitize_filename(ESTABLISHMENT_NAME)
    file_name = f"{sanitized_name}_{TARGET_STARS}_estrela.xlsx"
    file_path = output_path / file_name

    columns = [
        "estrelas",
        "nome_da_pessoa",
        "avaliacao",
        "data_avaliacao",
    ]
    dataframe = pd.DataFrame(reviews, columns=columns)
    dataframe.to_excel(file_path, index=False, engine="openpyxl")

    log_info("Excel generated successfully")
    return file_path


def main() -> None:
    log_info("Starting scraper...")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=HEADLESS)
            context = browser.new_context(locale=BROWSER_LOCALE)
            try:
                page = context.new_page()
                page.set_default_timeout(WAIT_TIMEOUT)
                page.set_default_navigation_timeout(WAIT_TIMEOUT)

                search_establishment(page)
                open_reviews(page)
                sort_by_latest(page)
                filter_by_stars(page)
                reviews = scroll_until_n_reviews(page)

                if not reviews:
                    log_warning("No reviews collected with the selected filters.")
                save_excel(reviews)
            finally:
                context.close()
                browser.close()
    except Exception as exc:  # noqa: BLE001
        log_error(f"Execution failed: {exc}")
        raise


if __name__ == "__main__":
    main()
