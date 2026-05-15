import calendar
import re
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote_plus

import pandas as pd
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

ESTABLISHMENT_NAME = "Magic Kingdom"
TARGET_STARS = 2
MAX_REVIEWS = 50
OUTPUT_FOLDER = "./output"
HEADLESS = False
WAIT_TIMEOUT = 30000
BROWSER_CHANNEL = "msedge"

MAPS_LANGUAGE_CODE = "pt-BR"
GOOGLE_MAPS_URL = f"https://www.google.com/maps?hl={MAPS_LANGUAGE_CODE}"
BROWSER_LOCALE = "pt-BR"
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
POST_ACTION_WAIT_MS = 1500
SCROLL_WAIT_MS = 2000
MAX_IDLE_SCROLLS = 10
DATE_NOT_INFORMED = "Não informado"
DATE_OUTPUT_FORMAT = "%d/%m/%y"
SEARCH_LOAD_WAIT_MS = 10000
POLL_INTERVAL_MS = 300
MAX_REVIEW_BUTTON_CANDIDATES = 5
REVIEWS_READY_WAIT_MULTIPLIER = 4
REVIEW_SCAN_WINDOW = 150
END_OF_LIST_MARKERS = (
    "You've reached the end of the list.",
    "You reached the end of the list.",
    "Você chegou ao final da lista.",
)
UNWANTED_URL_KEYWORDS = ("accounts.google.com", "/maps/contrib/")
UNSAFE_REVIEW_ACTION_KEYWORDS = (
    "fazer login",
    "write a review",
    "escrever uma avaliação",
    "escrever uma avaliacao",
    "avaliar",
)

TODAY_KEYWORDS = ("hoje", "today", "just now", "agora")
YESTERDAY_KEYWORDS = ("ontem", "yesterday")
SINGULAR_QUANTITY_WORDS = ("um", "uma", "a", "an", "one")

TIME_UNIT_ALIASES = {
    "min": "minute",
    "mins": "minute",
    "minute": "minute",
    "minutes": "minute",
    "minuto": "minute",
    "minutos": "minute",
    "h": "hour",
    "hr": "hour",
    "hrs": "hour",
    "hour": "hour",
    "hours": "hour",
    "hora": "hour",
    "horas": "hour",
    "day": "day",
    "days": "day",
    "dia": "day",
    "dias": "day",
    "week": "week",
    "weeks": "week",
    "semana": "week",
    "semanas": "week",
    "month": "month",
    "months": "month",
    "mes": "month",
    "meses": "month",
    "year": "year",
    "years": "year",
    "ano": "year",
    "anos": "year",
}

MONTH_NAME_ALIASES = {
    "jan": 1,
    "janeiro": 1,
    "january": 1,
    "fev": 2,
    "fevereiro": 2,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "marco": 3,
    "march": 3,
    "abr": 4,
    "abril": 4,
    "apr": 4,
    "april": 4,
    "mai": 5,
    "maio": 5,
    "may": 5,
    "jun": 6,
    "junho": 6,
    "june": 6,
    "jul": 7,
    "julho": 7,
    "july": 7,
    "ago": 8,
    "agosto": 8,
    "aug": 8,
    "august": 8,
    "set": 9,
    "setembro": 9,
    "sep": 9,
    "september": 9,
    "out": 10,
    "outubro": 10,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "novembro": 11,
    "november": 11,
    "dez": 12,
    "dezembro": 12,
    "dec": 12,
    "december": 12,
}

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
    "[jsaction*='reviewChart.moreReviews']",
    "button[jsaction*='tabs.tabClick'][aria-label*='Avaliações de']",
    "button[jsaction*='tabs.tabClick'][aria-label*='Avaliacoes de']",
    "button[jsaction*='tabs.tabClick'][aria-label*='Reviews for']",
    "button[jsaction*='pane.rating.moreReviews']",
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
EXPAND_REVIEW_BUTTON_SELECTORS = (
    "button[jsaction*='review.expandReview']",
    "button.w8nwRe[aria-label*='Ver mais']",
    "button.w8nwRe[aria-label*='More']",
)


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


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(character for character in normalized if unicodedata.category(character) != "Mn")


def format_review_date(value: date) -> str:
    return value.strftime(DATE_OUTPUT_FORMAT)


def subtract_months(reference_date: date, months: int) -> date:
    total_months = reference_date.year * 12 + (reference_date.month - 1) - months
    target_year = total_months // 12
    target_month = (total_months % 12) + 1
    max_day = calendar.monthrange(target_year, target_month)[1]
    target_day = min(reference_date.day, max_day)
    return date(target_year, target_month, target_day)


def subtract_years(reference_date: date, years: int) -> date:
    target_year = reference_date.year - years
    max_day = calendar.monthrange(target_year, reference_date.month)[1]
    target_day = min(reference_date.day, max_day)
    return date(target_year, reference_date.month, target_day)


def parse_absolute_review_date(text_ascii: str) -> Optional[date]:
    normalized = re.sub(r"[.,]", "", text_ascii)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    numeric_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", normalized)
    if numeric_match:
        day, month, year = numeric_match.groups()
        year_int = int(year)
        if year_int < 100:
            year_int += 2000
        try:
            return date(year_int, int(month), int(day))
        except ValueError:
            return None

    month_first_match = re.search(r"\b([a-z]+)\s+(\d{1,2})(?:\s+|,\s*)(\d{4})\b", normalized)
    if month_first_match:
        month_name, day, year = month_first_match.groups()
        month_number = MONTH_NAME_ALIASES.get(month_name)
        if month_number:
            try:
                return date(int(year), month_number, int(day))
            except ValueError:
                return None

    day_first_match = re.search(r"\b(\d{1,2})\s+(?:de\s+)?([a-z]+)\s+(?:de\s+)?(\d{4})\b", normalized)
    if day_first_match:
        day, month_name, year = day_first_match.groups()
        month_number = MONTH_NAME_ALIASES.get(month_name)
        if month_number:
            try:
                return date(int(year), month_number, int(day))
            except ValueError:
                return None

    return None


def parse_relative_review_date(text_ascii: str, reference_date: date) -> Optional[date]:
    normalized = re.sub(r"\s+", " ", text_ascii).strip()
    if not normalized:
        return None

    if any(keyword in normalized for keyword in TODAY_KEYWORDS):
        return reference_date

    if any(keyword in normalized for keyword in YESTERDAY_KEYWORDS):
        return reference_date - timedelta(days=1)

    relative_match = re.search(r"(ha\s+)?(\d+|um|uma|a|an|one)\s+([a-z]+)", normalized)
    if not relative_match:
        return None

    quantity_token = relative_match.group(2)
    unit_token = relative_match.group(3)

    if quantity_token.isdigit():
        quantity = int(quantity_token)
    elif quantity_token in SINGULAR_QUANTITY_WORDS:
        quantity = 1
    else:
        return None

    unit = TIME_UNIT_ALIASES.get(unit_token)
    if not unit:
        return None

    if unit == "minute" or unit == "hour":
        return reference_date
    if unit == "day":
        return reference_date - timedelta(days=quantity)
    if unit == "week":
        return reference_date - timedelta(weeks=quantity)
    if unit == "month":
        return subtract_months(reference_date, quantity)
    if unit == "year":
        return subtract_years(reference_date, quantity)
    return None


def normalize_review_date(raw_review_date: str, reference_date: Optional[date] = None) -> str:
    review_date = normalize_text(raw_review_date)
    if not review_date:
        return DATE_NOT_INFORMED

    base_date = reference_date or date.today()
    text_ascii = strip_accents(review_date.lower())

    parsed_relative_date = parse_relative_review_date(text_ascii, base_date)
    if parsed_relative_date:
        return format_review_date(parsed_relative_date)

    parsed_absolute_date = parse_absolute_review_date(text_ascii)
    if parsed_absolute_date:
        return format_review_date(parsed_absolute_date)

    return DATE_NOT_INFORMED


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
                    if is_unsafe_reviews_action(button):
                        continue
                    if not is_likely_reviews_open_button(button):
                        continue

                    previous_url = page.url
                    button.click(timeout=WAIT_TIMEOUT)
                    page.wait_for_timeout(POST_ACTION_WAIT_MS)
                    close_unwanted_popup_pages(page)

                    if is_unwanted_reviews_navigation(page.url):
                        log_warning("Unexpected navigation while opening reviews. Returning to place page.")
                        page.go_back(wait_until="domcontentloaded", timeout=WAIT_TIMEOUT)
                        page.wait_for_timeout(POST_ACTION_WAIT_MS)
                        if page.url == previous_url:
                            continue
                        continue

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


def has_reached_end_of_reviews(page: Page) -> bool:
    for marker in END_OF_LIST_MARKERS:
        try:
            if page.get_by_text(marker, exact=False).first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def is_unwanted_reviews_navigation(url: str) -> bool:
    lowered_url = (url or "").lower()
    return any(keyword in lowered_url for keyword in UNWANTED_URL_KEYWORDS)


def is_unsafe_reviews_action(button: Locator) -> bool:
    try:
        button_text = normalize_text(button.inner_text()).lower()
    except Exception:  # noqa: BLE001
        button_text = ""
    try:
        button_aria = normalize_text(button.get_attribute("aria-label")).lower()
    except Exception:  # noqa: BLE001
        button_aria = ""

    content = f"{button_text} {button_aria}"
    return any(keyword in content for keyword in UNSAFE_REVIEW_ACTION_KEYWORDS)


def is_likely_reviews_open_button(button: Locator) -> bool:
    try:
        jsaction = normalize_text(button.get_attribute("jsaction")).lower()
    except Exception:  # noqa: BLE001
        jsaction = ""

    if not jsaction:
        return False

    if "reviewchart.morereviews" in jsaction or "pane.rating.morereviews" in jsaction:
        return True

    if "tabs.tabclick" in jsaction:
        try:
            button_text = normalize_text(button.inner_text()).lower()
        except Exception:  # noqa: BLE001
            button_text = ""
        try:
            button_aria = normalize_text(button.get_attribute("aria-label")).lower()
        except Exception:  # noqa: BLE001
            button_aria = ""
        content = f"{button_text} {button_aria}"
        return "avalia" in content or "review" in content

    return False


def close_unwanted_popup_pages(page: Page) -> None:
    for opened_page in page.context.pages:
        if opened_page == page:
            continue
        try:
            if is_unwanted_reviews_navigation(opened_page.url):
                opened_page.close()
        except Exception:  # noqa: BLE001
            continue


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

    raw_review_date = get_text_from_selectors(review_card, REVIEW_DATE_SELECTORS)
    review_date = normalize_review_date(raw_review_date)

    return {
        "estrelas": stars,
        "nome_da_pessoa": reviewer_name,
        "avaliacao": review_text,
        "data_avaliacao": review_date,
    }


def build_review_key(review_data: dict) -> str:
    return "|".join(
        [
            normalize_text(review_data.get("nome_da_pessoa", "")),
            normalize_text(review_data.get("data_avaliacao", "")),
            str(review_data.get("estrelas", "")),
            normalize_text(review_data.get("avaliacao", "")),
        ]
    )


def scroll_until_n_reviews(page: Page) -> list[dict]:
    log_info("Collecting reviews...")
    reviews_feed = wait_for_reviews_container(page)

    collected_reviews: list[dict] = []
    seen_all_reviews: set[str] = set()
    seen_target_reviews: set[str] = set()
    seen_target_reviews_with_comment: set[str] = set()
    seen_collected_reviews: set[str] = set()
    idle_scrolls = 0
    total_reviews_analyzed = 0
    total_target_star_reviews_analyzed = 0
    total_target_star_reviews_with_comment = 0
    stop_reason = "unknown"

    while len(collected_reviews) < MAX_REVIEWS and idle_scrolls < MAX_IDLE_SCROLLS:
        if has_reached_end_of_reviews(page):
            stop_reason = "all_reviews_analyzed"
            break

        review_cards = page.locator(REVIEW_CARD_SELECTOR)
        review_count = review_cards.count()
        previous_count = len(collected_reviews)
        previous_scroll_height = reviews_feed.evaluate("(element) => element.scrollHeight")
        previous_scroll_top = reviews_feed.evaluate("(element) => element.scrollTop")

        start_index = max(0, review_count - REVIEW_SCAN_WINDOW)

        for index in range(start_index, review_count):
            review_card = review_cards.nth(index)
            review_data = extract_review(review_card)
            if not review_data:
                continue

            review_key = build_review_key(review_data)
            has_comment = bool(normalize_text(review_data["avaliacao"]))

            if review_key not in seen_all_reviews:
                seen_all_reviews.add(review_key)
                total_reviews_analyzed += 1

            if review_data["estrelas"] == TARGET_STARS and review_key not in seen_target_reviews:
                seen_target_reviews.add(review_key)
                total_target_star_reviews_analyzed += 1

            if review_data["estrelas"] == TARGET_STARS and has_comment and review_key not in seen_target_reviews_with_comment:
                seen_target_reviews_with_comment.add(review_key)
                total_target_star_reviews_with_comment += 1

            if review_data["estrelas"] != TARGET_STARS or not has_comment:
                continue

            if review_key in seen_collected_reviews:
                continue

            collected_reviews.append(review_data)
            seen_collected_reviews.add(review_key)
            log_info(f"Collected {len(collected_reviews)}/{MAX_REVIEWS} reviews")

            if len(collected_reviews) >= MAX_REVIEWS:
                stop_reason = "max_reviews_reached"
                break

        reviews_feed.evaluate("(element) => { element.scrollTop = element.scrollHeight; }")
        page.wait_for_timeout(SCROLL_WAIT_MS)
        new_review_count = page.locator(REVIEW_CARD_SELECTOR).count()
        new_scroll_height = reviews_feed.evaluate("(element) => element.scrollHeight")
        new_scroll_top = reviews_feed.evaluate("(element) => element.scrollTop")

        if has_reached_end_of_reviews(page):
            stop_reason = "all_reviews_analyzed"
            break

        loaded_new_content = (
            new_review_count > review_count
            or new_scroll_height > previous_scroll_height
            or new_scroll_top > previous_scroll_top
        )

        if len(collected_reviews) == previous_count and not loaded_new_content:
            idle_scrolls += 1
        else:
            idle_scrolls = 0

    if stop_reason == "unknown" and idle_scrolls >= MAX_IDLE_SCROLLS:
        stop_reason = "all_reviews_analyzed"

    log_info(f"Total de todas avaliações analisadas: {total_reviews_analyzed}")
    log_info(f"Total de avaliações de {TARGET_STARS} estrelas analisadas: {total_target_star_reviews_analyzed}")
    log_info(
        f"{total_target_star_reviews_with_comment} de {total_target_star_reviews_analyzed} "
        f"avaliações de {TARGET_STARS} estrelas com comentarios disponiveis"
    )
    if stop_reason == "all_reviews_analyzed":
        log_info("Coleta interrompida porque todas as avaliações disponiveis ja foram analisadas")

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
            browser = playwright.chromium.launch(channel=BROWSER_CHANNEL, headless=HEADLESS)
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
