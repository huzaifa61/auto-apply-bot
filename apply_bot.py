#!/usr/bin/env python3

from __future__ import annotations

import utils.net
import utils.clients
import utils.http_session
import utils.transport
import utils.session
import argparse
import json
import os
import random
import re
import sys
import time

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from playwright.sync_api import (
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)


# ==========================================================================
# CONFIG
# ==========================================================================

ROOT = Path(__file__).parent

CONFIG_DIR = ROOT / "config"
LOGS_DIR = ROOT / "logs"

LOGS_DIR.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")

APPS_SCRIPT_URL = os.environ.get(
    "APPS_SCRIPT_URL",
    "",
)

CHROME_PROFILE_DIR = Path(
    os.environ.get(
        "CHROME_PROFILE_DIR",
        str(
            Path.home()
            / ".linkedin-apply-bot-profile"
        ),
    )
)


# ==========================================================================
# CONFIG LOADING
# ==========================================================================

def load_json(path: Path) -> dict:
    if not path.exists():
        print(f"Missing config file: {path}")
        sys.exit(1)

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as e:
        print(
            f"Invalid JSON in {path}: {e}"
        )
        sys.exit(1)


@dataclass
class AnswerBank:
    profile: dict
    rules: list = field(
        default_factory=list
    )

    @classmethod
    def load(cls) -> "AnswerBank":

        profile = load_json(
            CONFIG_DIR / "profile.json"
        )

        answers = load_json(
            CONFIG_DIR / "answer_bank.json"
        )

        return cls(
            profile=profile,
            rules=answers.get(
                "rules",
                []
            ),
        )

    def match(
        self,
        label: str,
    ) -> Optional[dict]:

        normalized_label = _normalize(
            label
        )

        for rule in self.rules:

            for pattern in rule.get(
                "patterns",
                []
            ):

                if (
                    _normalize(pattern)
                    in normalized_label
                ):
                    return rule

        return None


def _normalize(
    value: str,
) -> str:

    text = str(value).strip().lower()
    text = text.replace('–', '-')
    text = text.replace('—', '-')
    text = text.replace('’', "'")
    return re.sub(
        r"\s+",
        " ",
        text,
    )


# ==========================================================================
# GOOGLE SHEET
# ==========================================================================

def fetch_queue(
    max_items: int,
) -> list[dict]:

    if not APPS_SCRIPT_URL:

        print(
            "APPS_SCRIPT_URL is not set in .env"
        )

        sys.exit(1)

    response = requests.get(
        APPS_SCRIPT_URL,
        params={
            "action": "queue"
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):

        print(
            "Sheet returned an error:",
            data.get("error"),
        )

        sys.exit(1)

    jobs = [
        job
        for job in data.get(
            "jobs",
            []
        )
        if job.get("url")
    ]

    return jobs[:max_items]


def update_sheet_status(
    job: dict,
    status: str,
    reason: str = "",
) -> None:

    payload = {
        "action": "updateStatus",
        "url": job["url"],
        "status": status,
        "reason": reason,
        "appliedAt": datetime.now().isoformat(
            timespec="seconds"
        ),
    }

    try:

        requests.post(
            APPS_SCRIPT_URL,
            json=payload,
            timeout=30,
        )

    except requests.RequestException as e:

        print(
            f"Warning: could not update sheet: {e}"
        )


# ==========================================================================
# SELECTORS
# ==========================================================================

EASY_APPLY_SELECTORS = [
    "button.jobs-apply-button",
    "button:has-text('Easy Apply')",
]

NEXT_SELECTORS = [
    "button:has-text('Next')",
    "button[aria-label='Next']",
    "button:has-text('Continue to next step')",
]

REVIEW_SELECTORS = [
    "button:has-text('Review')",
]

SUBMIT_SELECTORS = [
    "button:has-text('Submit application')",
]

DISCARD_SELECTORS = [
    "button:has-text('Discard')",
]


# ==========================================================================
# RESULT
# ==========================================================================

@dataclass
class JobResult:
    job: dict
    status: str
    reason: str = ""
    screenshot: Optional[str] = None


# ==========================================================================
# BASIC HELPERS
# ==========================================================================

def find_easy_apply_button(
    page: Page,
):

    for selector in EASY_APPLY_SELECTORS:

        try:

            elements = page.locator(
                selector
            )

            for i in range(
                elements.count()
            ):

                element = elements.nth(i)

                if not element.is_visible():
                    continue

                if not element.is_enabled():
                    continue

                return element

        except Exception:
            continue

    return None


# ==========================================================================
# EASY APPLY MODAL
# ==========================================================================

def get_easy_apply_modal(
    page: Page,
):

    # Prefer LinkedIn's Easy Apply modal.
    try:

        modals = page.locator(
            "div.jobs-easy-apply-modal"
        )

        for i in range(
            modals.count()
        ):

            modal = modals.nth(i)

            if modal.is_visible():
                return modal

    except Exception:
        pass

    # Fallback to visible dialogs.
    try:

        dialogs = page.locator(
            "div[role='dialog']"
        )

        for i in range(
            dialogs.count()
        ):

            dialog = dialogs.nth(i)

            if not dialog.is_visible():
                continue

            text = _normalize(
                dialog.inner_text()
            )

            if (
                "save this application"
                in text
            ):
                continue

            return dialog

    except Exception:
        pass

    return page.locator("body")


# ==========================================================================
# IGNORED LINKEDIN FIELDS
# ==========================================================================

def should_ignore_field(
    label: str,
) -> bool:

    normalized = _normalize(
        label
    )

    ignored_fields = [
        "email",
        "phone country code",
        "country code",
        "select language",
    ]

    return any(
        item in normalized
        for item in ignored_fields
    )


# ==========================================================================
# SAVE APPLICATION POPUP
# ==========================================================================

def find_save_application_dialog(
    page: Page,
):

    try:

        dialogs = page.locator(
            "div[role='dialog']"
        )

        for i in range(
            dialogs.count()
        ):

            dialog = dialogs.nth(i)

            if not dialog.is_visible():
                continue

            text = _normalize(
                dialog.inner_text()
            )

            if (
                "save this application"
                in text
                and "discard"
                in text
                and "save"
                in text
            ):

                return dialog

    except Exception:
        pass

    return None


def is_save_application_popup_visible(
    page: Page,
) -> bool:

    return (
        find_save_application_dialog(page)
        is not None
    )


def close_save_application_popup(
    page: Page,
) -> bool:
    """
    Close LinkedIn's Save this application popup.

    NEVER click Discard.
    """

    dialog = find_save_application_dialog(
        page
    )

    if dialog is None:
        return True

    print(
        "   Save application popup detected"
    )

    selectors = [
        "button.artdeco-modal__dismiss",
        "button[data-test-modal-close-btn]",
        "button[aria-label='Dismiss']",
        "button[aria-label='Close']",
        "button[aria-label='close']",
    ]

    # ----------------------------------------------------------------------
    # Preferred LinkedIn close button
    # ----------------------------------------------------------------------

    for selector in selectors:

        try:

            buttons = dialog.locator(
                selector
            )

            for i in range(
                buttons.count()
            ):

                button = buttons.nth(i)

                if not button.is_visible():
                    continue

                if not button.is_enabled():
                    continue

                button.click(
                    timeout=3000
                )

                page.wait_for_timeout(
                    700
                )

                if not is_save_application_popup_visible(
                    page
                ):

                    print(
                        "   Save popup dismissed"
                    )

                    return True

        except Exception:
            continue

    # ----------------------------------------------------------------------
    # Generic close button detection
    # ----------------------------------------------------------------------

    try:

        buttons = dialog.locator(
            "button"
        )

        for i in range(
            buttons.count()
        ):

            button = buttons.nth(i)

            if not button.is_visible():
                continue

            aria = (
                button.get_attribute(
                    "aria-label"
                )
                or ""
            )

            title = (
                button.get_attribute(
                    "title"
                )
                or ""
            )

            class_name = (
                button.get_attribute(
                    "class"
                )
                or ""
            )

            metadata = _normalize(
                f"{aria} {title} {class_name}"
            )

            # Never click Save or Discard.
            if "save" in metadata:
                continue

            if "discard" in metadata:
                continue

            if (
                "dismiss" in metadata
                or "close" in metadata
            ):

                try:

                    button.click(
                        force=True,
                        timeout=3000
                    )

                    page.wait_for_timeout(
                        700
                    )

                    if not is_save_application_popup_visible(
                        page
                    ):

                        print(
                            "   Save popup dismissed"
                        )

                        return True

                except Exception:
                    continue

    except Exception:
        pass

    # ----------------------------------------------------------------------
    # Escape fallback
    # ----------------------------------------------------------------------

    try:

        page.keyboard.press(
            "Escape"
        )

        page.wait_for_timeout(
            700
        )

        if not is_save_application_popup_visible(
            page
        ):

            print(
                "   Save popup dismissed with Escape"
            )

            return True

    except Exception:
        pass

    print(
        "   ERROR: could not dismiss Save popup"
    )

    return False


# ==========================================================================
# ACTIVE BUTTON
# ==========================================================================

def get_active_button(
    page: Page,
    selectors: list[str],
):

    modal = get_easy_apply_modal(
        page
    )

    for selector in selectors:

        try:

            buttons = modal.locator(
                selector
            )

            for i in range(
                buttons.count()
            ):

                button = buttons.nth(i)

                if not button.is_visible():
                    continue

                if not button.is_enabled():
                    continue

                return button

        except Exception:
            continue

    return None


# ==========================================================================
# PAGE INDICATOR
# ==========================================================================

def get_easy_apply_page(
    page: Page,
) -> str:

    try:

        modal = get_easy_apply_modal(
            page
        )

        text = modal.inner_text()

        match = re.search(
            r"(\d+)\s*/\s*(\d+)\s*pages?",
            text,
            flags=re.IGNORECASE,
        )

        if match:

            return (
                f"{match.group(1)}/"
                f"{match.group(2)}"
            )

    except Exception:
        pass

    return ""



def _radio_group_text_from_container(container) -> str:
    """
    Extract the question text for a radio group.

    LinkedIn sometimes puts only the Yes/No option labels inside the
    tight radio container while the actual question text is in its
    parent. We therefore try the current container first and then walk
    upward until useful question text is found.
    """

    def clean_text(raw_text: str, scope) -> str:
        if not raw_text:
            return ""

        lines = [
            line.strip()
            for line in raw_text.splitlines()
            if line.strip()
        ]

        option_texts = {
            "yes",
            "no",
            "true",
            "false",
        }

        # Include actual radio values / labels as option noise.
        try:
            radios = scope.locator("input[type='radio']")
            for i in range(radios.count()):
                radio = radios.nth(i)

                for attr in ("value", "aria-label"):
                    value = radio.get_attribute(attr) or ""
                    if value.strip():
                        option_texts.add(_normalize(value))

                rid = radio.get_attribute("id") or ""
                if rid:
                    labels = scope.locator(f"label[for='{rid}']")
                    for j in range(labels.count()):
                        value = labels.nth(j).inner_text().strip()
                        if value:
                            option_texts.add(_normalize(value))
        except Exception:
            pass

        useful = []

        for line in lines:
            normalized = _normalize(line)

            if normalized in option_texts:
                continue

            if normalized in {
                "this field is required",
                "this field is required.",
            }:
                continue

            if normalized.startswith("not sure how to answer"):
                continue

            useful.append(line)

        return " ".join(useful).strip()

    # First try the tight radio container itself.
    try:
        text = clean_text(container.inner_text().strip(), container)
        if text:
            return text
    except Exception:
        pass

    # IMPORTANT: the question can be a sibling/parent of the tight radio
    # wrapper. Walk upward without changing the container used for clicking.
    for level in range(1, 7):
        try:
            parent = container.locator(
                f"xpath=ancestor::*[{level}]"
            )

            if not parent.count():
                continue

            candidate = parent.first
            raw = candidate.inner_text().strip()

            if not raw:
                continue

            question = clean_text(raw, candidate)

            if not question:
                continue

            # Avoid returning an enormous modal/page-level container.
            if len(question) > 400:
                continue

            return question

        except Exception:
            continue

    return ""


def _mark_radio_group(container, group_id: str) -> None:
    """Mark a DOM container so it can be located reliably later."""
    try:
        container.evaluate(
            """
            (el, id) => {
                el.setAttribute('data-linkedin-bot-radio-group', id);
            }
            """,
            group_id,
        )
    except Exception:
        pass


def _find_radio_group_container(page: Page, radio, group_index: int):
    """
    Find the smallest visible ancestor that contains exactly two visible
    native radio inputs. This works for LinkedIn's custom DOM where there
    may be no useful fieldset/legend/name.
    """
    try:
        group_id = radio.evaluate(
            """
            (radio, index) => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' &&
                           style.visibility !== 'hidden' &&
                           rect.width > 0 &&
                           rect.height > 0;
                };

                let node = radio.parentElement;

                while (node) {
                    const radios = Array.from(
                        node.querySelectorAll('input[type="radio"]')
                    ).filter(isVisible);

                    if (radios.length === 2) {
                        const texts = (node.innerText || '').trim();
                        if (texts && /yes|no/i.test(texts)) {
                            const id = `radio-group-${Date.now()}-${index}`;
                            node.setAttribute(
                                'data-linkedin-bot-radio-group',
                                id
                            );
                            return id;
                        }
                    }

                    node = node.parentElement;
                }

                return '';
            }
            """,
            group_index,
        )

        if group_id:
            return page.locator(
                f"[data-linkedin-bot-radio-group='{group_id}']"
            ).first

    except Exception:
        pass

    return None


def _radio_selected(container, choice: str) -> bool:
    want = _normalize(choice)

    # Native checked radio.
    try:
        checked = container.locator(
            "input[type='radio']:checked"
        )

        for i in range(checked.count()):
            radio = checked.nth(i)

            value = _normalize(
                radio.get_attribute("value") or ""
            )
            aria = _normalize(
                radio.get_attribute("aria-label") or ""
            )

            if want in {value, aria}:
                return True

            rid = radio.get_attribute("id") or ""
            if rid:
                labels = container.locator(
                    f"label[for='{rid}']"
                )
                for j in range(labels.count()):
                    if _normalize(
                        labels.nth(j).inner_text()
                    ) == want:
                        return True
    except Exception:
        pass

    # ARIA custom radio.
    try:
        checked = container.locator(
            "[role='radio'][aria-checked='true']"
        )

        for i in range(checked.count()):
            radio = checked.nth(i)
            metadata = _normalize(
                " ".join(
                    [
                        radio.get_attribute("aria-label") or "",
                        radio.get_attribute("data-value") or "",
                        radio.inner_text() or "",
                    ]
                )
            )

            if want in metadata:
                return True
    except Exception:
        pass

    return False


def _radio_option(container, choice: str):
    """Return the clickable option element for a choice."""
    want = _normalize(choice)

    # 1. Associated labels.
    try:
        radios = container.locator(
            "input[type='radio']"
        )

        for i in range(radios.count()):
            radio = radios.nth(i)
            rid = radio.get_attribute("id") or ""
            if not rid:
                continue

            labels = container.locator(
                f"label[for='{rid}']"
            )

            for j in range(labels.count()):
                label = labels.nth(j)
                if not label.is_visible():
                    continue
                if _normalize(label.inner_text()) == want:
                    return label
    except Exception:
        pass

    # 2. Native accessible radio.
    try:
        radios = container.get_by_role(
            "radio",
            name=choice,
            exact=True,
        )

        for i in range(radios.count()):
            radio = radios.nth(i)
            if radio.is_visible():
                return radio
    except Exception:
        pass

    # 3. Custom role radio.
    try:
        radios = container.locator("[role='radio']")
        for i in range(radios.count()):
            radio = radios.nth(i)
            if not radio.is_visible():
                continue

            metadata = _normalize(
                " ".join(
                    [
                        radio.get_attribute("aria-label") or "",
                        radio.get_attribute("data-value") or "",
                        radio.inner_text() or "",
                    ]
                )
            )

            if want in metadata:
                return radio
    except Exception:
        pass

    # 4. Exact visible text.
    try:
        nodes = container.get_by_text(
            choice,
            exact=True,
        )

        for i in range(nodes.count()):
            node = nodes.nth(i)
            if node.is_visible():
                return node
    except Exception:
        pass

    return None



def _find_radio_container_from_question_node(node):
    """
    Starting from a visible question node, walk upward until finding the
    smallest ancestor containing exactly two visible native radio inputs.
    """
    try:
        container_id = node.evaluate(
            """
            (node) => {
                const visible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s.display !== 'none' &&
                           s.visibility !== 'hidden' &&
                           r.width > 0 &&
                           r.height > 0;
                };

                let el = node;

                for (let depth = 0; depth < 12 && el; depth++, el = el.parentElement) {
                    const radios = Array.from(
                        el.querySelectorAll('input[type="radio"]')
                    ).filter(visible);

                    if (radios.length !== 2) {
                        continue;
                    }

                    const text = (el.innerText || '').trim();

                    if (!text) {
                        continue;
                    }

                    const id =
                        'answer-bank-radio-' +
                        Date.now() +
                        '-' +
                        Math.random().toString(36).slice(2);

                    el.setAttribute(
                        'data-linkedin-bot-answer-radio',
                        id
                    );

                    return id;
                }

                return '';
            }
            """
        )

        if container_id:
            return node.page.locator(
                f"[data-linkedin-bot-answer-radio='{container_id}']"
            ).first

    except Exception:
        pass

    return None


def _radio_question_candidates(
    page: Page,
    modal,
    pattern: str,
):
    """
    Find visible DOM nodes containing an answer-bank pattern.
    Prefer the smallest visible nodes because their ancestor is usually
    the actual question container rather than the entire modal.
    """
    candidates = []

    try:
        regex = re.compile(
            re.escape(pattern),
            re.IGNORECASE,
        )

        nodes = modal.get_by_text(
            regex,
            exact=False,
        )

        for i in range(nodes.count()):
            node = nodes.nth(i)

            try:
                if not node.is_visible():
                    continue

                text = node.inner_text().strip()

                if not text:
                    continue

                candidates.append(
                    (
                        len(text),
                        node,
                    )
                )

            except Exception:
                continue

    except Exception:
        pass

    candidates.sort(
        key=lambda item: item[0]
    )

    return [
        node
        for _, node in candidates
    ]


def _mark_radio_common_container(page: Page, radio_one, radio_two, group_id: str):
    """Mark the lowest common ancestor of two radios."""
    try:
        marker1 = f"bot-radio-a-{group_id}"
        marker2 = f"bot-radio-b-{group_id}"

        radio_one.evaluate(
            "(el, id) => el.setAttribute('data-bot-radio-marker', id)",
            marker1,
        )
        radio_two.evaluate(
            "(el, id) => el.setAttribute('data-bot-radio-marker', id)",
            marker2,
        )

        container_id = f"bot-radio-container-{group_id}"

        radio_one.evaluate(
            """
            (el, args) => {
                const other = document.querySelector(
                    `[data-bot-radio-marker=\"${args.other}\"]`
                );
                if (!other) return false;

                let node = el;
                while (node) {
                    if (node.contains(other)) {
                        node.setAttribute(
                            'data-bot-radio-container',
                            args.container
                        );
                        return true;
                    }
                    node = node.parentElement;
                }
                return false;
            }
            """,
            {"other": marker2, "container": container_id},
        )

        return page.locator(
            f"[data-bot-radio-container='{container_id}']"
        ).first

    except Exception:
        return None


def _visible_question_nodes(modal):
    """Return small visible text elements that can contain questions."""
    try:
        nodes = modal.locator(
            "label, legend, p, span, div"
        )
    except Exception:
        return []

    results = []

    for i in range(nodes.count()):
        try:
            node = nodes.nth(i)
            if not node.is_visible():
                continue

            text = node.inner_text().strip()
            if not text:
                continue

            # Ignore very large containers. We want the smallest element
            # that contains the question text.
            if len(text) > 300:
                continue

            box = node.bounding_box()
            if not box:
                continue

            results.append((len(text), box, node, text))
        except Exception:
            continue

    results.sort(key=lambda item: item[0])
    return results


def _discover_answerable_radio_groups_from_bank(
    page: Page,
    bank: AnswerBank,
):
    """
    Robust answer-bank-driven radio discovery.

    LinkedIn sometimes renders the question text as a sibling of the radio
    container. In that case walking upward from the question node cannot find
    the radios. This implementation therefore also uses screen geometry:
    it finds the visible question text, finds the two visible radio inputs,
    and associates the nearest radio pair with that question.
    """
    modal = get_easy_apply_modal(page)

    try:
        radios_locator = modal.locator(
            "input[type='radio']"
        )
    except Exception:
        return []

    visible_radios = []

    for i in range(radios_locator.count()):
        try:
            radio = radios_locator.nth(i)
            if not radio.is_visible():
                continue
            box = radio.bounding_box()
            if not box:
                continue
            visible_radios.append((radio, box))
        except Exception:
            continue

    if len(visible_radios) < 2:
        return []

    # --------------------------------------------------------------
    # Build radio groups.
    # First by identical name where available.
    # Then by adjacent screen position for anonymous radios.
    # --------------------------------------------------------------
    groups = []
    used = set()

    named = {}
    anonymous = []

    for radio, box in visible_radios:
        try:
            name = (radio.get_attribute("name") or "").strip()
        except Exception:
            name = ""

        if name:
            named.setdefault(name, []).append((radio, box))
        else:
            anonymous.append((radio, box))

    for name, items in named.items():
        if len(items) == 2:
            groups.append(items)
            used.update(id(x[0]) for x in items)

    remaining = [item for item in visible_radios if id(item[0]) not in used]
    remaining.sort(key=lambda item: (item[1]["y"], item[1]["x"]))

    # Pair nearby anonymous radios. LinkedIn's Yes/No radio pair is normally
    # vertically adjacent and much closer to each other than to another
    # question's radio pair.
    i = 0
    while i < len(remaining) - 1:
        first = remaining[i]
        second = remaining[i + 1]

        y_gap = abs(
            (second[1]["y"] + second[1]["height"] / 2)
            -
            (first[1]["y"] + first[1]["height"] / 2)
        )
        x_gap = abs(
            (second[1]["x"] + second[1]["width"] / 2)
            -
            (first[1]["x"] + first[1]["width"] / 2)
        )

        # A Yes/No pair is usually within ~100px vertically.
        if y_gap <= 120 and x_gap <= 600:
            groups.append([first, second])
            i += 2
        else:
            i += 1

    if not groups:
        return []

    question_nodes = _visible_question_nodes(modal)
    discovered = []
    seen_questions = set()

    # --------------------------------------------------------------
    # For every answer-bank choice, locate its question by geometry.
    # --------------------------------------------------------------
    for rule in bank.rules:
        choice = rule.get("choice")
        if choice is None:
            continue

        choice = str(choice).strip()
        if not choice:
            continue

        for pattern in rule.get("patterns", []):
            pattern = str(pattern).strip()
            if not pattern:
                continue

            normalized_pattern = _normalize(pattern)
            matched_nodes = []

            for length, box, node, text in question_nodes:
                normalized_text = _normalize(text)
                if normalized_pattern in normalized_text:
                    matched_nodes.append(
                        (length, box, node, text)
                    )

            if not matched_nodes:
                continue

            # Prefer the smallest text node containing the exact pattern.
            matched_nodes.sort(key=lambda item: item[0])
            q_len, q_box, q_node, q_text = matched_nodes[0]
            q_center_y = q_box["y"] + q_box["height"] / 2
            q_center_x = q_box["x"] + q_box["width"] / 2

            best_group = None
            best_distance = float("inf")

            for group in groups:
                centers = [
                    (
                        item[1]["x"] + item[1]["width"] / 2,
                        item[1]["y"] + item[1]["height"] / 2,
                    )
                    for item in group
                ]

                group_y = sum(c[1] for c in centers) / len(centers)
                group_x = sum(c[0] for c in centers) / len(centers)

                distance = abs(group_y - q_center_y) + 0.25 * abs(
                    group_x - q_center_x
                )

                if distance < best_distance:
                    best_distance = distance
                    best_group = group

            if best_group is None:
                continue

            # Mark the common DOM container containing this pair.
            gid = f"{abs(hash((_normalize(q_text), pattern)))}"
            container = _mark_radio_common_container(
                page,
                best_group[0][0],
                best_group[1][0],
                gid,
            )

            if container is None:
                continue

            label = q_text.strip()
            key = _normalize(label)

            if key in seen_questions:
                continue

            seen_questions.add(key)

            print(
                f"   Radio group detected from answer bank: '{label}'"
            )

            discovered.append((label, container))
            break

    return discovered

def _answerable_radio_groups(page: Page, bank: AnswerBank):
    """
    Discover every visible radio question.

    Strategy:
      1. Use structural discovery when LinkedIn exposes a usable container.
      2. If a group is still missed, use answer_bank.json patterns to locate
         the visible question and then find its two-option radio container.
    """
    modal = get_easy_apply_modal(page)

    try:
        radios = modal.locator(
            "input[type='radio']"
        )

        total = radios.count()

    except Exception:
        return []

    print(
        f"   Total radio inputs found: {total}"
    )

    if total == 0:
        return []

    groups = []
    seen = set()

    # ------------------------------------------------------------------
    # Structural discovery
    # ------------------------------------------------------------------

    for index in range(total):

        try:
            radio = radios.nth(index)

            if not radio.is_visible():
                continue

            container = _find_radio_group_container(
                page,
                radio,
                index,
            )

            if container is None:
                continue

            visible_native_count = 0

            contained_radios = container.locator(
                "input[type='radio']"
            )

            for j in range(
                contained_radios.count()
            ):

                try:

                    if contained_radios.nth(j).is_visible():
                        visible_native_count += 1

                except Exception:
                    continue

            if visible_native_count != 2:
                continue

            label = _radio_group_text_from_container(
                container
            )

            if not label:
                continue

            if should_ignore_field(label):
                continue

            key = _normalize(label)

            if key in seen:
                continue

            seen.add(key)

            groups.append(
                (
                    label,
                    container,
                )
            )

        except Exception as e:

            print(
                f"   Radio discovery failed: {e}"
            )

    # ------------------------------------------------------------------
    # Answer-bank-driven fallback
    # ------------------------------------------------------------------

    fallback_groups = (
        _discover_answerable_radio_groups_from_bank(
            page,
            bank,
        )
    )

    for label, container in fallback_groups:

        key = _normalize(label)

        if key in seen:
            continue

        seen.add(key)

        groups.append(
            (
                label,
                container,
            )
        )

    return groups

def _click_radio_from_question_text(
    page: Page,
    modal,
    question_pattern: str,
    choice: str,
) -> bool:
    """
    Last-resort radio handler for LinkedIn layouts where the question text
    is outside the smallest DOM container containing the Yes/No controls.

    This is answer-bank driven: question_pattern and choice come only from
    answer_bank.json. It does not hardcode any job answer.
    """
    pattern = str(question_pattern or "").strip()
    choice = str(choice or "").strip()
    if not pattern or not choice:
        return False

    try:
        question_nodes = modal.get_by_text(
            re.compile(re.escape(pattern), re.IGNORECASE),
            exact=False,
        )
    except Exception:
        return False

    if question_nodes.count() == 0:
        return False

    # Collect visible question nodes, preferring small text nodes.
    q_candidates = []
    for i in range(question_nodes.count()):
        try:
            node = question_nodes.nth(i)
            if not node.is_visible():
                continue
            box = node.bounding_box()
            text = node.inner_text().strip()
            if not box or not text:
                continue
            q_candidates.append((len(text), box, node, text))
        except Exception:
            continue

    q_candidates.sort(key=lambda x: x[0])

    if not q_candidates:
        return False

    try:
        choice_nodes = modal.get_by_text(
            choice,
            exact=True,
        )
    except Exception:
        return False

    if choice_nodes.count() == 0:
        return False

    visible_choices = []
    for i in range(choice_nodes.count()):
        try:
            node = choice_nodes.nth(i)
            if not node.is_visible():
                continue
            box = node.bounding_box()
            if not box:
                continue
            visible_choices.append((node, box))
        except Exception:
            continue

    if not visible_choices:
        return False

    # Try each matching question node. For each question, choose the closest
    # visible Yes/No text that is below it in the modal.
    for _, q_box, q_node, q_text in q_candidates:
        q_x = q_box["x"] + q_box["width"] / 2
        q_y = q_box["y"] + q_box["height"] / 2

        ranked = []
        for choice_node, c_box in visible_choices:
            c_x = c_box["x"] + c_box["width"] / 2
            c_y = c_box["y"] + c_box["height"] / 2

            # Prefer options below the question. If not below, still allow
            # them but with a heavy penalty.
            vertical_penalty = 0 if c_y >= q_y else 5000
            distance = (
                vertical_penalty
                + abs(c_y - q_y)
                + 0.25 * abs(c_x - q_x)
            )
            ranked.append((distance, choice_node, c_box))

        ranked.sort(key=lambda x: x[0])

        for _, choice_node, _ in ranked[:4]:
            try:
                # First try the nearest clickable ancestor.
                clickable = choice_node.locator(
                    "xpath=ancestor::*["
                    "self::label or self::button or "
                    "@role='radio' or @role='button'"
                    "][1]"
                )

                target = None
                if clickable.count() and clickable.first.is_visible():
                    target = clickable.first

                if target is None:
                    target = choice_node

                try:
                    target.scroll_into_view_if_needed()
                except Exception:
                    pass

                print(
                    f"      Direct radio click from answer-bank question: "
                    f"'{pattern}' -> '{choice}'"
                )

                target.click(force=True, timeout=3000)
                page.wait_for_timeout(600)

                # ------------------------------------------------------
                # Verify by finding a nearby container with two radios.
                # ------------------------------------------------------
                verified = False

                for level in range(1, 9):
                    try:
                        ancestor = choice_node.locator(
                            f"xpath=ancestor::*[{level}]"
                        )
                        if not ancestor.count():
                            continue

                        radios = ancestor.locator(
                            "input[type='radio']"
                        )

                        if radios.count() != 2:
                            continue

                        checked = ancestor.locator(
                            "input[type='radio']:checked"
                        )

                        if checked.count() > 0:
                            verified = True
                            break
                    except Exception:
                        continue

                if verified:
                    print(
                        f"      -> RADIO ACCEPTED: {choice}"
                    )
                    return True

                # ARIA/custom radio verification.
                try:
                    checked = modal.locator(
                        "[role='radio'][aria-checked='true']"
                    )
                    if checked.count() > 0:
                        print(
                            f"      -> RADIO ACCEPTED: {choice}"
                        )
                        return True
                except Exception:
                    pass

            except Exception:
                continue

    return False


def handle_radio_questions(
    page: Page,
    bank: AnswerBank,
) -> bool:
    """
    Answer every visible radio question using answer_bank.json only.

    Important: this function first uses the answer-bank question text itself
    to locate and click the radio option. That avoids depending on LinkedIn's
    inconsistent fieldset/legend/container structure when text fields and
    radio questions coexist on the same page.
    """
    modal = get_easy_apply_modal(page)

    # ------------------------------------------------------------------
    # First: answer-bank-driven direct question-text strategy.
    # ------------------------------------------------------------------
    handled_questions = set()
    direct_attempted = False

    for rule in bank.rules:
        choice = rule.get("choice")
        if choice is None:
            continue

        choice = str(choice).strip()
        if not choice:
            continue

        patterns = rule.get("patterns", [])
        for pattern in patterns:
            pattern = str(pattern).strip()
            if not pattern:
                continue

            # Determine whether the question text is actually visible.
            try:
                nodes = modal.get_by_text(
                    re.compile(re.escape(pattern), re.IGNORECASE),
                    exact=False,
                )
                visible = False
                for i in range(nodes.count()):
                    try:
                        if nodes.nth(i).is_visible():
                            visible = True
                            break
                    except Exception:
                        continue
                if not visible:
                    continue
            except Exception:
                continue

            key = _normalize(pattern)
            if key in handled_questions:
                continue

            handled_questions.add(key)
            direct_attempted = True

            print(
                f"   Answer-bank radio question detected: "
                f"'{pattern}'"
            )
            print(
                f"      Answer-bank choice: {choice}"
            )

            if _click_radio_from_question_text(
                page,
                modal,
                pattern,
                choice,
            ):
                break

            print(
                f"      Direct radio click failed for '{pattern}'"
            )

    # ------------------------------------------------------------------
    # Second: structural fallback for any radio groups not found above.
    # ------------------------------------------------------------------
    groups = _answerable_radio_groups(page, bank)

    if groups:
        for label, container in groups:
            key = _normalize(label)

            # Direct strategy may already have handled it.
            already = any(
                key in handled_key or handled_key in key
                for handled_key in handled_questions
            )

            rule = bank.match(label)

            if already and rule is None:
                continue

            if not rule:
                continue

            choice = rule.get("choice")
            if choice is None or not str(choice).strip():
                continue

            choice = str(choice).strip()

            print(
                f"   Radio question: '{label}'"
            )
            print(
                f"      Answer-bank choice: {choice}"
            )

            if _radio_selected(container, choice):
                print(
                    "      -> EXISTING RADIO ACCEPTED"
                )
                continue

            option = _radio_option(container, choice)
            if option is None:
                print(
                    f"      Could not locate choice '{choice}'"
                )
                continue

            try:
                option.scroll_into_view_if_needed()
            except Exception:
                pass

            clicked = False

            try:
                clickable = option.locator(
                    "xpath=ancestor::*["
                    "self::label or self::button or "
                    "@role='radio' or @role='button'"
                    "][1]"
                )
                if clickable.count() and clickable.first.is_visible():
                    clickable.first.click(force=True, timeout=3000)
                    clicked = True
            except Exception:
                pass

            if not clicked:
                try:
                    option.click(force=True, timeout=3000)
                    clicked = True
                except Exception:
                    pass

            if clicked:
                page.wait_for_timeout(600)

            if _radio_selected(container, choice):
                print(
                    f"      -> RADIO ACCEPTED: {choice}"
                )
            else:
                print(
                    f"      -> RADIO FAILED: could not verify '{choice}'"
                )

    # ------------------------------------------------------------------
    # Final validation: if a required radio error is visible, do not let
    # the caller proceed to Review/Next.
    # ------------------------------------------------------------------
    try:
        modal_text = _normalize(modal.inner_text())
    except Exception:
        modal_text = ""

    # If there are visible radio inputs but the page still contains a
    # required-field error, this radio handling failed.
    try:
        radio_count = modal.locator(
            "input[type='radio']"
        ).count()
    except Exception:
        radio_count = 0

    if radio_count > 0:
        required_error = (
            "this field is required" in modal_text
        )

        if required_error:
            print(
                "   ERROR: required radio field is still unanswered"
            )
            return False

    if not direct_attempted and not groups:
        print(
            "   No answerable radio groups detected."
        )

    return True


# ==========================================================================
# RESUME HELPERS
# ==========================================================================

def get_resume_path(
    bank: AnswerBank,
) -> Optional[Path]:

    resume_path = bank.profile.get(
        "resume_path"
    )

    if not resume_path:
        print(
            "   ERROR: resume_path is not configured "
            "in config/profile.json"
        )
        return None

    resume_file = (
        Path(resume_path)
        .expanduser()
        .resolve()
    )

    print(
        f"   Resume path: {resume_file}"
    )

    if not resume_file.exists():

        print(
            f"   ERROR: resume file does not exist: "
            f"{resume_file}"
        )

        return None

    if not resume_file.is_file():

        print(
            f"   ERROR: resume path is not a file: "
            f"{resume_file}"
        )

        return None

    file_size = resume_file.stat().st_size

    print(
        f"   Resume size: {file_size} bytes"
    )

    return resume_file


def is_resume_required(
    page: Page,
) -> bool:

    try:

        modal = get_easy_apply_modal(
            page
        )

        text = _normalize(
            modal.inner_text()
        )

        return (
            "a resume is required"
            in text
        )

    except Exception:

        return False


def is_resume_page(
    page: Page,
) -> bool:
    """Detect an actual visible LinkedIn Resume upload page.

    A persistent hidden input[type=file] is NOT sufficient because
    LinkedIn keeps the file input in the DOM across Easy Apply pages.
    """
    try:
        modal = get_easy_apply_modal(page)

        upload_controls = modal.locator(
            "button:has-text('Upload resume'), "
            "button:has-text('Upload Resume'), "
            "[role='button']:has-text('Upload resume'), "
            "[role='button']:has-text('Upload Resume')"
        )

        for i in range(upload_controls.count()):
            control = upload_controls.nth(i)
            if control.is_visible():
                return True

        if _visible_text_exists(page, "A resume is required"):
            return True

    except Exception:
        pass

    return False


def _visible_text_exists(
    page: Page,
    text: str,
) -> bool:
    """
    Check the visible LinkedIn UI for exact text.

    This is intentionally stricter than searching modal.inner_text(),
    because LinkedIn can keep hidden/old validation text in the DOM.
    """
    try:
        locator = page.get_by_text(
            text,
            exact=True,
        )

        for i in range(locator.count()):
            element = locator.nth(i)

            try:
                if element.is_visible():
                    return True
            except Exception:
                continue

    except Exception:
        pass

    return False


def _visible_resume_filename(
    page: Page,
    resume_file: Path,
) -> bool:
    """
    Verify the uploaded resume is actually shown by LinkedIn UI.
    """
    filename = resume_file.name.strip()

    if _visible_text_exists(
        page,
        filename,
    ):
        return True

    # Sometimes LinkedIn displays filename without exact DOM text.
    try:
        modal = get_easy_apply_modal(page)

        locator = modal.get_by_text(
            re.escape(filename),
            exact=False,
        )

        for i in range(locator.count()):
            element = locator.nth(i)

            if element.is_visible():
                return True

    except Exception:
        pass

    return False


def _resume_input_has_file(
    page: Page,
) -> bool:
    """
    Check actual FileList on any resume input.
    """
    selectors = [
        "div.jobs-easy-apply-modal input[type='file']",
        "div[role='dialog'] input[type='file']",
        "input[type='file']",
    ]

    for selector in selectors:
        try:
            inputs = page.locator(selector)

            for i in range(inputs.count()):
                element = inputs.nth(i)

                try:
                    count = element.evaluate(
                        """
                        el => el.files ? el.files.length : 0
                        """
                    )

                    if int(count or 0) > 0:
                        return True

                except Exception:
                    continue

        except Exception:
            continue

    return False


def is_resume_required(
    page: Page,
) -> bool:
    """
    Check the VISIBLE validation message.
    """
    return _visible_text_exists(
        page,
        "A resume is required",
    )


def resume_upload_verified(
    page: Page,
    resume_file: Path,
) -> bool:
    """
    STRICT verification.

    Upload is accepted only when:
      1. The visible 'A resume is required' message is gone.
      2. The resume filename is visible OR the file input contains a file.

    We never treat set_input_files() itself as proof of success.
    """

    # Give LinkedIn's React state time to update.
    page.wait_for_timeout(1200)

    if is_resume_required(page):
        print(
            "   Resume verification: FAILED - "
            "'A resume is required' is still visible"
        )
        return False

    filename_visible = _visible_resume_filename(
        page,
        resume_file,
    )

    file_present = _resume_input_has_file(
        page
    )

    print(
        f"   Resume verification: "
        f"error_visible={is_resume_required(page)}, "
        f"filename_visible={filename_visible}, "
        f"file_input_has_file={file_present}"
    )

    if not filename_visible and not file_present:
        return False

    # Extra safeguard: error must remain absent.
    if is_resume_required(page):
        return False

    return True


def _dispatch_file_events(
    page: Page,
    file_input,
) -> None:
    """
    Trigger input/change events after a direct file assignment.
    """
    try:
        file_input.dispatch_event("input")
    except Exception:
        pass

    try:
        file_input.dispatch_event("change")
    except Exception:
        pass

    page.wait_for_timeout(700)


def upload_resume_once(
    page: Page,
    resume_file: Path,
) -> bool:
    """
    One upload attempt.

    Priority:
      1. Visible Upload resume control + file chooser.
      2. Direct file input fallback.

    Returns True ONLY if LinkedIn visibly accepts the resume.
    """

    print(
        f"   Upload attempt for: {resume_file.name}"
    )

    # ======================================================================
    # METHOD 1 - VISIBLE UPLOAD RESUME BUTTON
    # ======================================================================

    upload_selectors = [
        "button:has-text('Upload resume')",
        "button:has-text('Upload Resume')",
        "[role='button']:has-text('Upload resume')",
        "[role='button']:has-text('Upload Resume')",
        "label:has-text('Upload resume')",
    ]

    for selector in upload_selectors:
        try:
            buttons = page.locator(selector)

            for i in range(buttons.count()):
                button = buttons.nth(i)

                if not button.is_visible():
                    continue

                if not button.is_enabled():
                    continue

                print(
                    f"   Found visible upload control: {selector}"
                )

                try:
                    with page.expect_file_chooser(
                        timeout=6000
                    ) as chooser_info:
                        button.click()

                    chooser = chooser_info.value

                    chooser.set_files(
                        str(resume_file)
                    )

                    print(
                        "   File selected through LinkedIn upload control"
                    )

                    # Wait for LinkedIn to process upload.
                    deadline = time.time() + 8

                    while time.time() < deadline:

                        page.wait_for_timeout(500)

                        if resume_upload_verified(
                            page,
                            resume_file,
                        ):
                            print(
                                "   Resume accepted by LinkedIn"
                            )
                            return True

                        # Still invalid -> continue waiting.
                        if is_resume_required(page):
                            continue

                    print(
                        "   Visible uploader did not produce "
                        "a valid LinkedIn resume state"
                    )

                except Exception as e:
                    print(
                        f"   Visible upload control failed: {e}"
                    )

        except Exception:
            continue

    # ======================================================================
    # METHOD 2 - DIRECT FILE INPUT
    # ======================================================================

    print(
        "   Trying direct file input..."
    )

    input_selectors = [
        "div.jobs-easy-apply-modal input[type='file']",
        "div[role='dialog'] input[type='file']",
        "input[type='file']",
    ]

    for selector in input_selectors:

        try:
            inputs = page.locator(selector)

            count = inputs.count()

            if count == 0:
                continue

            print(
                f"   File inputs found: {count}"
            )

            for i in range(count):

                file_input = inputs.nth(i)

                try:
                    file_input.set_input_files(
                        str(resume_file)
                    )

                    _dispatch_file_events(
                        page,
                        file_input,
                    )

                    print(
                        f"   File assigned to input #{i + 1}"
                    )

                    deadline = time.time() + 8

                    while time.time() < deadline:

                        page.wait_for_timeout(500)

                        if resume_upload_verified(
                            page,
                            resume_file,
                        ):
                            print(
                                "   Resume accepted by LinkedIn"
                            )
                            return True

                except Exception as e:
                    print(
                        f"   Direct file input #{i + 1} failed: {e}"
                    )

        except Exception:
            continue

    return False


def upload_resume(
    page: Page,
    bank: AnswerBank,
) -> bool:
    """
    Upload resume with exactly ONE retry.

    We never report success unless the visible LinkedIn
    form state confirms it.
    """

    resume_file = get_resume_path(
        bank
    )

    if resume_file is None:
        return False

    # Attempt 1
    print(
        "   Resume upload attempt 1/2"
    )

    if upload_resume_once(
        page,
        resume_file,
    ):
        return True

    print(
        "   Resume upload attempt 1 failed"
    )

    # Reset focus / give LinkedIn time before retry.
    page.wait_for_timeout(800)

    # Attempt 2
    print(
        "   Resume upload attempt 2/2"
    )

    if upload_resume_once(
        page,
        resume_file,
    ):
        return True

    print(
        "   RESUME UPLOAD FAILED AFTER ONE RETRY"
    )

    return False


# ==========================================================================
# NEXT BUTTON
# ==========================================================================

def find_next_button(
    page: Page,
):

    # ----------------------------------------------------------------------
    # Exact role
    # ----------------------------------------------------------------------

    try:

        buttons = page.get_by_role(
            "button",
            name="Next",
            exact=True,
        )

        for i in range(
            buttons.count()
        ):

            button = buttons.nth(i)

            if not button.is_visible():
                continue

            if not button.is_enabled():
                continue

            return button

    except Exception:
        pass

    # ----------------------------------------------------------------------
    # Inside Easy Apply modal
    # ----------------------------------------------------------------------

    modal = get_easy_apply_modal(
        page
    )

    for selector in NEXT_SELECTORS:

        try:

            buttons = modal.locator(
                selector
            )

            for i in range(
                buttons.count()
            ):

                button = buttons.nth(i)

                if not button.is_visible():
                    continue

                if not button.is_enabled():
                    continue

                return button

        except Exception:
            continue

    # ----------------------------------------------------------------------
    # Global fallback
    # ----------------------------------------------------------------------

    for selector in NEXT_SELECTORS:

        try:

            buttons = page.locator(
                selector
            )

            for i in range(
                buttons.count()
            ):

                button = buttons.nth(i)

                if not button.is_visible():
                    continue

                if not button.is_enabled():
                    continue

                return button

        except Exception:
            continue

    return None


def click_next_once(
    page: Page,
) -> bool:

    if is_save_application_popup_visible(
        page
    ):

        print(
            "   Save popup detected before Next"
        )

        if not close_save_application_popup(
            page
        ):

            return False

        page.wait_for_timeout(
            400
        )

    button = find_next_button(
        page
    )

    if button is None:

        print(
            "   Next button not found"
        )

        return False

    print(
        "   Next button found"
    )

    try:

        button.scroll_into_view_if_needed()

    except Exception:
        pass

    # Normal.
    try:

        button.click(
            timeout=5000
        )

        print(
            "   Next clicked normally"
        )

        return True

    except Exception as e:

        print(
            f"   Normal Next click failed: {e}"
        )

    # Force.
    try:

        button.click(
            force=True,
            timeout=3000
        )

        print(
            "   Next clicked with force"
        )

        return True

    except Exception:
        pass

    # DOM.
    try:

        button.evaluate(
            "(el) => el.click()"
        )

        print(
            "   Next clicked using DOM"
        )

        return True

    except Exception:
        pass

    return False


def wait_for_navigation_result(
    page: Page,
    previous_page: str,
    timeout_seconds: float = 3.0,
) -> tuple[bool, str]:

    deadline = (
        time.time()
        + timeout_seconds
    )

    while time.time() < deadline:

        if is_save_application_popup_visible(
            page
        ):

            return (
                False,
                get_easy_apply_page(page)
            )

        current_page = get_easy_apply_page(
            page
        )

        if (
            current_page
            and previous_page
            and current_page != previous_page
        ):

            return (
                True,
                current_page
            )

        page.wait_for_timeout(
            250
        )

    current_page = get_easy_apply_page(
        page
    )

    if (
        current_page
        and previous_page
        and current_page != previous_page
    ):

        return (
            True,
            current_page
        )

    return (
        False,
        current_page
    )


def click_next_with_one_retry(
    page: Page,
) -> tuple[bool, bool]:

    previous_page = get_easy_apply_page(
        page
    )

    print(
        f"   Page before Next: "
        f"{previous_page or 'unknown'}"
    )

    # ======================================================================
    # ATTEMPT 1
    # ======================================================================

    print(
        "   Next attempt 1/2"
    )

    clicked = click_next_once(
        page
    )

    if clicked:

        page.wait_for_timeout(
            900
        )

        if is_save_application_popup_visible(
            page
        ):

            print(
                "   Save popup appeared after Next"
            )

            if not close_save_application_popup(
                page
            ):

                return (
                    False,
                    False
                )

        advanced, current_page = (
            wait_for_navigation_result(
                page,
                previous_page,
                timeout_seconds=2.5,
            )
        )

        print(
            f"   Page after attempt 1: "
            f"{current_page or 'unknown'}"
        )

        if advanced:

            print(
                "   Next succeeded on attempt 1"
            )

            return (
                True,
                False
            )

    # ======================================================================
    # RETRY ONCE
    # ======================================================================

    print(
        "   Retrying Next once..."
    )

    page.wait_for_timeout(
        700
    )

    if is_save_application_popup_visible(
        page
    ):

        if not close_save_application_popup(
            page
        ):

            return (
                False,
                True
            )

        page.wait_for_timeout(
            500
        )

    print(
        "   Next attempt 2/2"
    )

    clicked = click_next_once(
        page
    )

    if not clicked:

        print(
            "   Next retry click failed"
        )

        return (
            False,
            True
        )

    page.wait_for_timeout(
        1000
    )

    if is_save_application_popup_visible(
        page
    ):

        print(
            "   Save popup appeared after retry"
        )

        if not close_save_application_popup(
            page
        ):

            return (
                False,
                True
            )

        page.wait_for_timeout(
            500
        )

    advanced, current_page = (
        wait_for_navigation_result(
            page,
            previous_page,
            timeout_seconds=2.5,
        )
    )

    print(
        f"   Page after retry: "
        f"{current_page or 'unknown'}"
    )

    if advanced:

        print(
            "   Next succeeded on retry"
        )

        return (
            True,
            True
        )

    print(
        "   Next still did not advance"
    )

    return (
        False,
        True
    )


# ==========================================================================
# FORM FIELDS
# ==========================================================================

def get_visible_form_fields(
    page: Page,
) -> list:
    """
    Detect real Easy Apply fields.

    Important:
      * email is never processed
      * phone country code is never processed
      * Select language is never processed
      * resume is handled separately
      * radio questions are detected from the actual visible DOM,
        even when LinkedIn does not expose a useful fieldset/legend/name.
    """

    fields = []
    modal = get_easy_apply_modal(page)

    # ----------------------------------------------------------------------
    # TEXT / PHONE / NUMBER / TEXTAREA
    # ----------------------------------------------------------------------

    input_selector = (
        "input[type='text'],"
        "input[type='tel'],"
        "input[type='number'],"
        "textarea"
    )

    try:
        elements = modal.locator(input_selector).all()
    except Exception:
        elements = []

    for element in elements:
        try:
            if not element.is_visible():
                continue

            label = _label_for(page, element)

            if not label:
                continue

            if should_ignore_field(label):
                print(
                    f"   Ignoring field: '{label}'"
                )
                continue

            fields.append(
                (
                    label,
                    element,
                    "text",
                )
            )

        except Exception:
            continue

    # Radio questions are handled separately by handle_radio_questions().

    # ----------------------------------------------------------------------
    # SELECT
    # ----------------------------------------------------------------------

    try:
        selects = modal.locator("select").all()
    except Exception:
        selects = []

    for element in selects:
        try:
            if not element.is_visible():
                continue

            label = _label_for(page, element)

            if not label:
                continue

            if should_ignore_field(label):
                print(
                    f"   Ignoring auto-filled select: '{label}'"
                )
                continue

            fields.append(
                (
                    label,
                    element,
                    "select",
                )
            )

        except Exception:
            continue

    # Resume is handled separately.
    return fields

def _label_for(
    page: Page,
    element,
) -> str:

    try:

        native = element.evaluate(
            """
            el => (
                el.labels &&
                el.labels.length
            )
            ?
            Array.from(el.labels)
                .map(l => l.innerText)
                .join(' ')
            :
            ''
            """
        )

        if native and native.strip():
            return native.strip()

        aria = element.get_attribute(
            "aria-label"
        )

        if aria and aria.strip():
            return aria.strip()

        labelled_by = element.get_attribute(
            "aria-labelledby"
        )

        if labelled_by:

            texts = []

            for element_id in labelled_by.split():

                try:

                    locator = page.locator(
                        f"#{element_id}"
                    )

                    if locator.count():

                        text = (
                            locator.first
                            .inner_text()
                            .strip()
                        )

                        if text:
                            texts.append(
                                text
                            )

                except Exception:
                    continue

            if texts:
                return " ".join(
                    texts
                )

        element_id = element.get_attribute(
            "id"
        )

        if element_id:

            label = page.locator(
                f"label[for='{element_id}']"
            ).first

            if label.count():

                text = (
                    label.inner_text()
                    .strip()
                )

                if text:
                    return text

        placeholder = element.get_attribute(
            "placeholder"
        )

        if placeholder and placeholder.strip():
            return placeholder.strip()

        containers = [
            "xpath=ancestor::div[contains(@class,'form-element')][1]",
            "xpath=ancestor::div[contains(@class,'fb-form-element')][1]",
            "xpath=ancestor::div[contains(@class,'jobs-easy-apply-form-section')][1]",
        ]

        for selector in containers:

            try:

                container = element.locator(
                    selector
                )

                if not container.count():
                    continue

                text = (
                    container.inner_text()
                    .strip()
                )

                if not text:
                    continue

                lines = [
                    line.strip()
                    for line in text.split("\n")
                    if line.strip()
                ]

                if lines:
                    return lines[0]

            except Exception:
                continue

    except Exception:
        pass

    return ""


# ==========================================================================
# PHONE FIELD
# ==========================================================================

def _is_phone_field(
    label: str,
    element,
) -> bool:

    normalized = _normalize(
        label
    )

    keywords = [
        "phone number",
        "mobile number",
        "mobile phone",
        "mobile",
        "phone",
    ]

    if any(
        keyword in normalized
        for keyword in keywords
    ):
        return True

    try:

        return (
            (
                element.get_attribute(
                    "type"
                )
                or ""
            ).lower()
            == "tel"
        )

    except Exception:

        return False


# ==========================================================================
# INPUT VALUES
# ==========================================================================

def _get_input_value(
    element,
) -> str:

    try:

        value = element.evaluate(
            """
            el => {
                if (
                    el &&
                    typeof el.value === 'string'
                ) {
                    return el.value;
                }

                return '';
            }
            """
        )

        if value is not None:
            return str(value).strip()

    except Exception:
        pass

    try:

        value = element.input_value()

        if value is not None:
            return str(value).strip()

    except Exception:
        pass

    return ""


def _clear_field(
    element,
) -> None:

    element.click()

    # macOS
    try:

        element.press(
            "Meta+A"
        )

        element.press(
            "Backspace"
        )

        return

    except Exception:
        pass

    # Windows/Linux
    try:

        element.press(
            "Control+A"
        )

        element.press(
            "Backspace"
        )

    except Exception:
        pass


def _values_match(
    expected: str,
    actual: str,
    is_phone: bool = False,
) -> bool:

    if is_phone:

        expected = re.sub(
            r"\D",
            "",
            expected
        )

        actual = re.sub(
            r"\D",
            "",
            actual
        )

    return (
        expected.strip()
        == actual.strip()
    )


# ==========================================================================
# FILL NORMAL FIELD
# ==========================================================================

def fill_field(
    page: Page,
    label: str,
    element,
    kind: str,
    bank: AnswerBank,
) -> bool:

    # Never treat an unnamed DOM control as an application question.
    if not label or not label.strip():
        print(
            "      -> IGNORED (unnamed field)"
        )
        return True

    # Safety: ignored fields should never fail.
    if should_ignore_field(
        label
    ):

        print(
            "      -> IGNORED"
        )

        return True

    # All answer values, including phone numbers, come from answer_bank.json.

    # ----------------------------------------------------------------------
    # Find rule for all other fields
    # ----------------------------------------------------------------------

    rule = bank.match(
        label
    )

    if not rule:

        print(
            f"      No answer-bank rule "
            f"matched '{label}'"
        )

        return False

    # ----------------------------------------------------------------------
    # Text
    # ----------------------------------------------------------------------

    if kind == "text":

        expected = str(
            rule.get(
                "answer",
                ""
            )
        ).strip()

        if not expected:
            return False

        is_phone = _is_phone_field(
            label,
            element
        )

        try:

            element.scroll_into_view_if_needed()

            existing = _get_input_value(
                element
            )

            if existing:

                print(
                    f"      existing value: "
                    f"{existing}"
                )

                if _values_match(
                    expected,
                    existing,
                    is_phone=is_phone,
                ):

                    print(
                        "      -> EXISTING VALUE ACCEPTED"
                    )

                    return True

            print(
                f"      entering: {expected}"
            )

            _clear_field(
                element
            )

            if is_phone:

                element.press_sequentially(
                    expected,
                    delay=80
                )

            else:

                element.type(
                    expected,
                    delay=40
                )

            element.press(
                "Tab"
            )

            page.wait_for_timeout(
                600
            )

            actual = _get_input_value(
                element
            )

            print(
                f"      expected: {expected}"
            )

            print(
                f"      actual:   {actual}"
            )

            if _values_match(
                expected,
                actual,
                is_phone=is_phone,
            ):

                return True

            # One field-level retry.
            print(
                "      -> field retry"
            )

            element.click()

            _clear_field(
                element
            )

            if is_phone:

                element.press_sequentially(
                    expected,
                    delay=100
                )

            else:

                element.type(
                    expected,
                    delay=50
                )

            element.press(
                "Tab"
            )

            page.wait_for_timeout(
                700
            )

            actual = _get_input_value(
                element
            )

            return _values_match(
                expected,
                actual,
                is_phone=is_phone,
            )

        except Exception as e:

            print(
                f"   Failed to fill "
                f"'{label}': {e}"
            )

            return False

    # ----------------------------------------------------------------------
    # Select
    # ----------------------------------------------------------------------

    if kind == "select":

        choice = str(
            rule.get(
                "choice",
                ""
            )
        ).strip()

        if not choice:
            return False

        try:

            element.select_option(
                label=choice
            )

            print(
                f"      Selected: {choice}"
            )

            return True

        except Exception:
            pass

        try:

            options = element.locator(
                "option"
            )

            normalized_choice = _normalize(
                choice
            )

            for i in range(
                options.count()
            ):

                option = options.nth(i)

                text = (
                    option.inner_text()
                    .strip()
                )

                normalized_text = _normalize(
                    text
                )

                if (
                    normalized_text
                    == normalized_choice
                    or
                    normalized_choice
                    in normalized_text
                    or
                    normalized_text
                    in normalized_choice
                ):

                    value = (
                        option.get_attribute(
                            "value"
                        )
                    )

                    if value is not None:

                        element.select_option(
                            value=value
                        )

                        print(
                            f"      Selected: {text}"
                        )

                        return True

        except Exception as e:

            print(
                f"      Select failed: {e}"
            )

        return False

    # Radio questions are handled separately by handle_radio_questions().

    return False


# ==========================================================================
# SCREENSHOT    return False


# ==========================================================================
# SCREENSHOT
# ==========================================================================

def save_screenshot(
    page: Page,
    job: dict,
) -> Optional[str]:

    screenshot = (
        LOGS_DIR
        / (
            f"flag_"
            f"{_slug(job)}_"
            f"{int(time.time())}.png"
        )
    )

    try:

        page.screenshot(
            path=str(screenshot)
        )

        return str(screenshot)

    except Exception:

        return None


# ==========================================================================
# APPLY JOB
# ==========================================================================

def apply_to_job(
    page: Page,
    job: dict,
    bank: AnswerBank,
    dry_run: bool,
) -> JobResult:

    # ----------------------------------------------------------------------
    # Open job
    # ----------------------------------------------------------------------

    page.goto(
        job["url"],
        wait_until="domcontentloaded",
        timeout=45000,
    )

    page.wait_for_timeout(
        random.randint(
            1500,
            3000,
        )
    )

    # ----------------------------------------------------------------------
    # Easy Apply
    # ----------------------------------------------------------------------

    easy_apply = find_easy_apply_button(
        page
    )

    if not easy_apply:

        return JobResult(
            job,
            "skipped",
            "No Easy Apply button "
            "(external application)",
        )

    try:

        easy_apply.click()

    except Exception as e:

        return JobResult(
            job,
            "failed",
            f"Could not click Easy Apply: {e}",
        )

    page.wait_for_timeout(
        1500
    )

    # ----------------------------------------------------------------------
    # Steps
    # ----------------------------------------------------------------------

    max_steps = 12

    for step_number in range(
        1,
        max_steps + 1,
    ):

        print(
            "\n"
            + "=" * 65
        )

        print(
            f"Easy Apply step "
            f"{step_number}/{max_steps}"
        )

        print(
            "=" * 65
        )

        current_page = get_easy_apply_page(
            page
        )

        print(
            f"   Current Easy Apply page: "
            f"{current_page or 'unknown'}"
        )

        # ------------------------------------------------------------------
        # Close save popup if already visible
        # ------------------------------------------------------------------

        if is_save_application_popup_visible(
            page
        ):

            if not close_save_application_popup(
                page
            ):

                screenshot = save_screenshot(
                    page,
                    job
                )

                return JobResult(
                    job,
                    "skipped",
                    (
                        "Skipped: unable to dismiss "
                        "Save application popup"
                    ),
                    screenshot,
                )

        # ==================================================================
        # RESUME PAGE
        # ==================================================================

        if is_resume_page(page):

            resume_file = get_resume_path(
                bank
            )

            if resume_file is None:

                screenshot = save_screenshot(
                    page,
                    job
                )

                return JobResult(
                    job,
                    "skipped",
                    "Skipped: resume file is not configured",
                    screenshot,
                )

            print(
                "\n   Resume page detected."
            )

            # IMPORTANT:
            # Never assume the resume is already uploaded.
            # LinkedIn must visibly accept it before Next is attempted.
            if is_resume_required(page):
                print(
                    "   LinkedIn says: "
                    "'A resume is required'"
                )
            else:
                print(
                    "   No visible resume error yet; "
                    "checking upload state."
                )

            # Always run the dedicated uploader on this page.
            uploaded = upload_resume(
                page,
                bank
            )

            if not uploaded:

                print(
                    "   ERROR: LinkedIn did not accept the resume."
                )

                screenshot = save_screenshot(
                    page,
                    job
                )

                return JobResult(
                    job,
                    "skipped",
                    (
                        "Skipped: resume upload was not "
                        "accepted by LinkedIn"
                    ),
                    screenshot,
                )

            # Final strict verification immediately before Next.
            page.wait_for_timeout(800)

            if is_resume_required(page):

                print(
                    "   ERROR: 'A resume is required' "
                    "is STILL visible."
                )

                screenshot = save_screenshot(
                    page,
                    job
                )

                return JobResult(
                    job,
                    "skipped",
                    (
                        "Skipped: LinkedIn still requires "
                        "a resume"
                    ),
                    screenshot,
                )

            if not resume_upload_verified(
                page,
                resume_file
            ):

                print(
                    "   ERROR: resume could not be verified "
                    "in LinkedIn UI."
                )

                screenshot = save_screenshot(
                    page,
                    job
                )

                return JobResult(
                    job,
                    "skipped",
                    (
                        "Skipped: resume upload verification "
                        "failed"
                    ),
                    screenshot,
                )

            print(
                "   Resume visibly accepted by LinkedIn."
            )

# ==================================================================
        # RADIO QUESTIONS
        # ==================================================================

        if not handle_radio_questions(page, bank):

            screenshot = save_screenshot(
                page,
                job
            )

            return JobResult(
                job,
                "skipped",
                "Skipped: could not answer required radio question",
                screenshot,
            )

        # REAL FORM FIELDS ONLY
        # ==================================================================

        fields = get_visible_form_fields(
            page
        )

        print(
            f"\n   Fields to process: "
            f"{len(fields)}"
        )

        field_failed = False

        for (
            label,
            element,
            kind,
        ) in fields:

            print(
                f"\n   Field: '{label}'"
            )

            ok = fill_field(
                page,
                label,
                element,
                kind,
                bank
            )

            print(
                "      -> "
                + (
                    "ACCEPTED"
                    if ok
                    else "FAILED"
                )
            )

            if ok:
                continue

            # One field retry.
            print(
                "      -> retrying field once"
            )

            page.wait_for_timeout(
                500
            )

            retry_ok = fill_field(
                page,
                label,
                element,
                kind,
                bank
            )

            if retry_ok:

                print(
                    "      -> ACCEPTED ON RETRY"
                )

                continue

            field_failed = True

            print(
                f"      -> unable to process "
                f"'{label}'"
            )

            break

        # ------------------------------------------------------------------
        # Field failure = skip job
        # ------------------------------------------------------------------

        if field_failed:

            screenshot = save_screenshot(
                page,
                job
            )

            return JobResult(
                job,
                "skipped",
                (
                    "Skipped: unable to handle "
                    "required field"
                ),
                screenshot,
            )

        print(
            "\n   All processable fields completed."
        )

        # ------------------------------------------------------------------
        # If LinkedIn is still showing a required-field error, do not click
        # Review/Submit. This prevents looping past unanswered radio groups.
        # ------------------------------------------------------------------

        try:
            modal = get_easy_apply_modal(page)
            modal_text = _normalize(
                modal.inner_text()
            )

            required_visible = (
                _visible_text_exists(
                    page,
                    "This field is required"
                )
                or "this field is required" in modal_text
                or _visible_text_exists(
                    page,
                    "A resume is required"
                )
                or "a resume is required" in modal_text
            )
        except Exception:
            required_visible = False

        if required_visible:
            print(
                "   LinkedIn still shows a required field error."
            )
            screenshot = save_screenshot(page, job)
            return JobResult(
                job,
                "skipped",
                "Skipped: required application field remains unanswered",
                screenshot,
            )

        # ==================================================================
        # SUBMIT
        # ==================================================================

        submit = get_active_button(
            page,
            SUBMIT_SELECTORS
        )

        if submit:

            print(
                "   Submit button detected."
            )

            if dry_run:

                print(
                    "   DRY RUN: not submitting."
                )

                return JobResult(
                    job,
                    "skipped",
                    (
                        "Dry run: would have "
                        "submitted here"
                    ),
                )

            try:

                submit.click(
                    timeout=5000
                )

                page.wait_for_timeout(
                    2000
                )

                return JobResult(
                    job,
                    "applied"
                )

            except Exception as e:

                return JobResult(
                    job,
                    "failed",
                    f"Submit click failed: {e}",
                )

        # ==================================================================
        # REVIEW
        # ==================================================================

        review = get_active_button(
            page,
            REVIEW_SELECTORS
        )

        if review:

            try:

                review.click(
                    timeout=5000
                )

                print(
                    "   Review clicked."
                )

                page.wait_for_timeout(
                    1200
                )

                continue

            except Exception:
                pass

        # ==================================================================
        # NEXT
        # ==================================================================

        print(
            "\n   Attempting Next..."
        )

        success, retried = (
            click_next_with_one_retry(
                page
            )
        )

        if success:

            print(
                "   Next navigation successful."
            )

            page.wait_for_timeout(
                1000
            )

            next_page = get_easy_apply_page(
                page
            )

            print(
                f"   Current page after Next: "
                f"{next_page or 'unknown'}"
            )

            continue

        # ==================================================================
        # NEXT FAILED
        # ==================================================================

        print(
            "\n"
            + "!" * 65
        )

        print(
            "   NEXT FAILED AFTER ONE RETRY"
        )

        print(
            "   Skipping this job."
        )

        print(
            "!" * 65
        )

        screenshot = save_screenshot(
            page,
            job
        )

        return JobResult(
            job,
            "skipped",
            (
                "Skipped: Next did not advance "
                "after 1 retry"
            ),
            screenshot,
        )

    # ----------------------------------------------------------------------
    # Too many steps
    # ----------------------------------------------------------------------

    screenshot = save_screenshot(
        page,
        job
    )

    return JobResult(
        job,
        "skipped",
        (
            "Skipped: too many Easy Apply steps"
        ),
        screenshot,
    )


# ==========================================================================
# SLUG
# ==========================================================================

def _slug(
    job: dict,
) -> str:

    text = (
        job.get(
            "company",
            ""
        )
        + "-"
        + job.get(
            "jobTitle",
            ""
        )
    )

    return re.sub(
        r"[^a-z0-9]+",
        "-",
        text.lower()
    ).strip("-")[:40]


# ==========================================================================
# REPORT
# ==========================================================================

def write_report(
    results: list[JobResult],
    dry_run: bool,
) -> None:

    applied = [
        result
        for result in results
        if result.status == "applied"
    ]

    flagged = [
        result
        for result in results
        if result.status == "flagged"
    ]

    failed = [
        result
        for result in results
        if result.status == "failed"
    ]

    skipped = [
        result
        for result in results
        if result.status == "skipped"
    ]

    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H%M%S"
    )

    report_path = (
        LOGS_DIR
        / f"run-{timestamp}.json"
    )

    report_path.write_text(
        json.dumps(
            [
                result.__dict__
                for result in results
            ],
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(
        "\n"
        + "=" * 65
    )

    print(
        f"{'DRY RUN — ' if dry_run else ''}"
        f"Run summary — "
        f"{datetime.now():%Y-%m-%d %H:%M}"
    )

    print(
        f"  Applied:  {len(applied)}"
    )

    print(
        f"  Flagged:  {len(flagged)}"
    )

    print(
        f"  Failed:   {len(failed)}"
    )

    print(
        f"  Skipped:  {len(skipped)}"
    )

    if skipped:

        print(
            "\nSkipped jobs:"
        )

        for result in skipped:

            print(
                f"  - "
                f"{result.job.get('jobTitle')} "
                f"@ "
                f"{result.job.get('company')}"
            )

            print(
                f"    Reason: "
                f"{result.reason}"
            )

            if result.screenshot:

                print(
                    f"    Screenshot: "
                    f"{result.screenshot}"
                )

    if flagged:

        print(
            "\nFlagged jobs:"
        )

        for result in flagged:

            print(
                f"  - "
                f"{result.job.get('jobTitle')} "
                f"@ "
                f"{result.job.get('company')}"
            )

            print(
                f"    Reason: "
                f"{result.reason}"
            )

    if failed:

        print(
            "\nFailed jobs:"
        )

        for result in failed:

            print(
                f"  - "
                f"{result.job.get('jobTitle')} "
                f"@ "
                f"{result.job.get('company')}"
            )

            print(
                f"    Reason: "
                f"{result.reason}"
            )

    print(
        f"\nFull report saved to:"
        f"\n{report_path}"
    )

    print(
        "=" * 65
    )


# ==========================================================================
# MAIN
# ==========================================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--max",
        type=int,
        default=15,
        help="Maximum applications to attempt",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Fill forms but never "
            "submit final application"
        ),
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help=(
            "Run Chrome headless "
            "(not recommended for LinkedIn)"
        ),
    )

    args = parser.parse_args()

    bank = AnswerBank.load()

    queue = fetch_queue(
        args.max
    )

    if not queue:

        print(
            "No unprocessed jobs "
            "in the sheet right now."
        )

        return

    print(
        f"Found {len(queue)} job(s) "
        f"to process "
        f"(max={args.max}, "
        f"dry_run={args.dry_run})."
    )

    results: list[JobResult] = []

    with sync_playwright() as playwright:

        context = (
            playwright.chromium
            .launch_persistent_context(
                user_data_dir=str(
                    CHROME_PROFILE_DIR
                ),
                channel="chrome",
                headless=args.headless,
                viewport={
                    "width": 1280,
                    "height": 900,
                },
            )
        )

        page = (
            context.pages[0]
            if context.pages
            else context.new_page()
        )

        for index, job in enumerate(
            queue,
            1
        ):

            print(
                "\n"
                + "=" * 65
            )

            print(
                f"[{index}/{len(queue)}] "
                f"{job.get('jobTitle')} "
                f"@ "
                f"{job.get('company')}"
            )

            print(
                "=" * 65
            )

            try:

                result = apply_to_job(
                    page,
                    job,
                    bank,
                    args.dry_run,
                )

            except PWTimeout:

                result = JobResult(
                    job,
                    "failed",
                    "Page timed out",
                )

            except Exception as e:

                result = JobResult(
                    job,
                    "failed",
                    (
                        f"{type(e).__name__}: "
                        f"{e}"
                    ),
                )

            results.append(
                result
            )

            print(
                f"\nRESULT: "
                f"{result.status}"
            )

            if result.reason:

                print(
                    f"Reason: "
                    f"{result.reason}"
                )

            # ----------------------------------------------------------------
            # Update sheet only for actual non-skipped outcomes.
            # ----------------------------------------------------------------

            if (
                not args.dry_run
                and result.status != "skipped"
            ):

                update_sheet_status(
                    job,
                    result.status.capitalize(),
                    result.reason,
                )

            # ----------------------------------------------------------------
            # Delay
            # ----------------------------------------------------------------

            if index < len(queue):

                delay = random.uniform(
                    15,
                    45
                )

                print(
                    f"\nWaiting "
                    f"{delay:.1f}s "
                    f"before next job..."
                )

                time.sleep(
                    delay
                )

        context.close()

    write_report(
        results,
        args.dry_run,
    )


if __name__ == "__main__":
    main()