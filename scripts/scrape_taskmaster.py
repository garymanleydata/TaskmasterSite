import csv
import json
import os
import re
import time
import unicodedata
from bs4 import BeautifulSoup
import requests

ENABLE_LOCAL_CACHE = False
CACHE_DIR = ".cache"
FORCE_REFRESH = True


def clean_text(v_raw_text: str) -> str:
    if not v_raw_text:
        return ""
    v_cleaned = re.sub(r"\[\w+\]", "", v_raw_text)
    v_cleaned = (
        unicodedata.normalize("NFKC", v_cleaned)
        .replace("\xa0", " ")
        .replace("\u200b", "")
    )
    return re.sub(r"\s+", " ", v_cleaned).strip()


def fetch_series_html(v_series_id: int) -> str | None:
    v_cache_file = os.path.join(CACHE_DIR, f"series_{v_series_id}.html")

    if ENABLE_LOCAL_CACHE and not FORCE_REFRESH:
        if os.path.exists(v_cache_file):
            with open(v_cache_file, "r", encoding="utf-8") as f:
                return f.read()

    v_api_url = "https://taskmaster.fandom.com/api.php"
    v_params = {
        "action": "parse",
        "page": f"Series_{v_series_id}",
        "prop": "text",
        "format": "json",
    }
    v_headers = {
        "User-Agent": (
            "TaskmasterTelemetryBot/1.0 (https://github.com/your-username;"
            " contact@example.com)"
        )
    }

    v_retries = 3
    for v_attempt in range(1, v_retries + 1):
        try:
            v_resp = requests.get(
                v_api_url, params=v_params, headers=v_headers, timeout=20
            )
            if v_resp.status_code != 200:
                return None

            v_data = v_resp.json()
            if (
                "error" in v_data
                or "parse" not in v_data
                or "text" not in v_data["parse"]
            ):
                return None

            v_html = v_data["parse"]["text"]["*"]

            if ENABLE_LOCAL_CACHE:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(v_cache_file, "w", encoding="utf-8") as f:
                    f.write(v_html)

            return v_html

        except Exception as e:
            if v_attempt < v_retries:
                time.sleep(v_attempt * 2.0)
            else:
                return None


def expand_table_with_metadata(v_table) -> list[list[dict]]:
    v_grid = []
    v_rowspans = {}

    for v_tr in v_table.find_all("tr"):
        v_row = []
        v_col_idx = 0

        v_cells = v_tr.find_all(["td", "th"])
        v_cell_iter = iter(v_cells)

        while True:
            if v_col_idx in v_rowspans:
                v_remaining, v_inherited_dict = v_rowspans[v_col_idx]
                v_row.append({
                    "text": v_inherited_dict["text"],
                    "is_inherited_rowspan": True,
                    "rowspan": v_inherited_dict["rowspan"],
                    "raw_cell": v_inherited_dict["raw_cell"],
                })
                if v_remaining > 1:
                    v_rowspans[v_col_idx] = (v_remaining - 1, v_inherited_dict)
                else:
                    del v_rowspans[v_col_idx]
                v_col_idx += 1
                continue

            try:
                v_cell = next(v_cell_iter)
            except StopIteration:
                break

            v_text = clean_text(v_cell.get_text(separator=" ", strip=True))
            v_colspan = int(v_cell.get("colspan", 1))
            v_rowspan = int(v_cell.get("rowspan", 1))

            v_cell_dict = {
                "text": v_text,
                "is_inherited_rowspan": False,
                "rowspan": v_rowspan,
                "raw_cell": v_cell,
            }

            for _ in range(v_colspan):
                v_row.append(v_cell_dict)
                if v_rowspan > 1:
                    v_rowspans[v_col_idx] = (v_rowspan - 1, v_cell_dict)
                v_col_idx += 1

        if v_row:
            v_grid.append(v_row)

    return v_grid


def parse_series_from_api(v_series_id: int) -> tuple[list, int]:
    v_html = fetch_series_html(v_series_id)
    if not v_html:
        return [], 0

    v_soup = BeautifulSoup(v_html, "html.parser")
    v_tables = v_soup.find_all("table")

    df_series_tasks = []
    v_max_ep = 0

    for v_table in v_tables:
        v_table_class = " ".join(v_table.get("class", []))
        if any(
            k in v_table_class.lower()
            for k in ["navbox", "toc", "infobox", "sidebar"]
        ):
            continue

        v_grid = expand_table_with_metadata(v_table)
        if len(v_grid) < 3:
            continue

        v_contestants = []
        v_desc_col_idx = 1

        for v_header_row in v_grid[:3]:
            v_row_str = " ".join(cell["text"] for cell in v_header_row).lower()
            if "•" in v_row_str or "champion of champions" in v_row_str:
                continue

            for v_idx, v_cell_dict in enumerate(v_header_row):
                v_clean = clean_text(v_cell_dict["text"])
                if (
                    v_clean
                    and not any(
                        v_clean.lower().startswith(k)
                        for k in [
                            "episode",
                            "ep",
                            "series",
                            "task",
                            "total",
                            "prize",
                            "prev",
                            "next",
                            "broadcast",
                            "airdate",
                        ]
                    )
                    and not any(
                        v_clean.lower() == k
                        for k in [
                            "#",
                            "no.",
                            "no",
                            "description",
                            "pts",
                            "score",
                            "winner",
                            "title",
                            "viewers",
                            "date",
                            "tba",
                        ]
                    )
                    and not re.search(
                        r"\(\d{1,2}\s+[A-Za-z]+\s+\d{4}\)", v_clean
                    )
                    and len(v_clean) > 2
                ):
                    if not any(c["name"] == v_clean for c in v_contestants):
                        v_contestants.append(
                            {"name": v_clean, "col_idx": v_idx}
                        )

        if len(v_contestants) < 4:
            continue

        v_current_ep = 0
        v_task_num = 0
        dict_ep_scores = {c["name"]: 0 for c in v_contestants}
        v_tiebreak_winner = None
        df_ep_buffer = []

        for v_row_cells in v_grid:
            if not v_row_cells:
                continue

            v_row_text_list = [cell["text"] for cell in v_row_cells]
            v_row_str = " ".join(v_row_text_list)

            if "•" in v_row_str or "champion of champions" in v_row_str.lower():
                continue

            v_ep_match = re.search(
                r"^Episode\s+(\d+)", v_row_text_list[0], re.IGNORECASE
            ) or re.search(r"Episode\s+(\d+)", v_row_str, re.IGNORECASE)
            if v_ep_match and len(set(v_row_text_list)) <= 3:
                if dict_ep_scores and df_ep_buffer:
                    _resolve_ep_winner(
                        df_ep_buffer, dict_ep_scores, v_tiebreak_winner
                    )
                    df_series_tasks.extend(df_ep_buffer)
                    df_ep_buffer = []

                v_current_ep = int(v_ep_match.group(1))
                v_max_ep = max(v_max_ep, v_current_ep)
                v_task_num = 0
                dict_ep_scores = {c["name"]: 0 for c in v_contestants}
                v_tiebreak_winner = None
                continue

            if v_row_text_list[0].lower().startswith("total") or v_row_text_list[
                0
            ].lower().startswith("grand total"):
                if dict_ep_scores and df_ep_buffer:
                    _resolve_ep_winner(
                        df_ep_buffer, dict_ep_scores, v_tiebreak_winner
                    )
                    df_series_tasks.extend(df_ep_buffer)
                    df_ep_buffer = []
                    dict_ep_scores = {c["name"]: 0 for c in v_contestants}
                    v_tiebreak_winner = None
                continue

            if v_current_ep == 0:
                v_current_ep = 1
                v_max_ep = 1

            if (
                len(v_row_cells) > v_desc_col_idx
                and len(v_row_cells) >= len(v_contestants) + 2
            ):
                v_task_desc = v_row_cells[v_desc_col_idx]["text"]
            else:
                v_task_desc = v_row_cells[0]["text"]

            if (
                not v_task_desc
                or v_task_desc.lower()
                in ["#", "task", "description", "task description"]
                or v_task_desc.lower().startswith("episode ")
                or any(c["name"] == v_task_desc for c in v_contestants)
            ):
                continue

            v_contestant_cells = [
                v_row_cells[c["col_idx"]]
                for c in v_contestants
                if c["col_idx"] < len(v_row_cells)
            ]

            v_has_explicit_scores = any(
                not cell.get("is_inherited_rowspan", False)
                and re.search(r"-?\d+", cell["text"])
                for cell in v_contestant_cells
            )

            if not v_has_explicit_scores:
                if df_ep_buffer:
                    for v_item in df_ep_buffer[-len(v_contestants) :]:
                        v_item["task_description"] += f" | {v_task_desc}"
                continue

            v_task_num += 1
            v_desc_lower = v_task_desc.lower()
            v_is_tiebreak = (
                1
                if (
                    "tiebreak" in v_desc_lower
                    or v_task_desc.startswith("T.")
                    or v_task_desc.startswith("T ")
                    or v_row_text_list[0].upper() in ["T", "TB"]
                )
                else 0
            )

            if v_is_tiebreak:
                v_task_type = "Tiebreak"
            elif "prize" in v_desc_lower or (
                v_task_num == 1 and "bonus" not in v_desc_lower
            ):
                v_task_type = "Prize"
            elif (
                "live" in v_desc_lower
                or "final" in v_desc_lower
                or (
                    "studio" in v_desc_lower
                    and not v_desc_lower.startswith("make")
                )
            ):
                v_task_type = "Live"
            elif "team" in v_desc_lower:
                v_task_type = "Team"
            else:
                v_task_type = "Normal"

            for c in v_contestants:
                if c["col_idx"] >= len(v_row_cells):
                    continue

                v_cell_dict = v_row_cells[c["col_idx"]]
                v_cell_val = v_cell_dict["text"]
                v_is_inherited = v_cell_dict.get("is_inherited_rowspan", False)

                v_is_dq = (
                    1 if ("DQ" in v_cell_val or "DSQ" in v_cell_val) else 0
                )

                if v_is_tiebreak and any(
                    k in v_cell_val for k in ["✔", "✓", "win", "W", "1st"]
                ):
                    v_tiebreak_winner = c["name"]

                v_match = re.search(r"-?\d+", v_cell_val)
                if v_is_tiebreak or v_is_inherited:
                    v_pts = 0
                else:
                    v_pts = int(v_match.group(0)) if v_match else 0

                if not v_is_tiebreak:
                    dict_ep_scores[c["name"]] += v_pts

                df_ep_buffer.append({
                    "series_id": v_series_id,
                    "episode_num": v_current_ep,
                    "task_num": v_task_num,
                    "task_description": v_task_desc,
                    "task_type": v_task_type,
                    "contestant_name": c["name"],
                    "points": v_pts,
                    "is_dq": v_is_dq,
                    "is_tiebreaker": v_is_tiebreak,
                    "is_ep_winner": 0,
                })

        if dict_ep_scores and df_ep_buffer:
            _resolve_ep_winner(df_ep_buffer, dict_ep_scores, v_tiebreak_winner)
            df_series_tasks.extend(df_ep_buffer)

    return df_series_tasks, v_max_ep


def _resolve_ep_winner(df_buffer, dict_scores, v_tiebreak_winner):
    if not dict_scores:
        return
    v_max = max(dict_scores.values())
    v_top = [k for k, v in dict_scores.items() if v == v_max]
    v_winner = v_top[0]
    if len(v_top) > 1 and v_tiebreak_winner in v_top:
        v_winner = v_tiebreak_winner

    for v_item in df_buffer:
        if v_item["contestant_name"] == v_winner:
            v_item["is_ep_winner"] = 1


def build_series_aggregates(df_telemetry: list[dict]) -> list[dict]:
    dict_groups = {}

    for row in df_telemetry:
        key = (row["series_id"], row["contestant_name"])
        if key not in dict_groups:
            dict_groups[key] = {
                "series_id": row["series_id"],
                "contestant_name": row["contestant_name"],
                "total_points": 0,
                "prize_points": 0,
                "normal_points": 0,
                "team_points": 0,
                "live_points": 0,
                "tasks_attempted": 0,
                "episodes_won": set(),
                "tiebreak_wins": 0,
                "dq_count": 0,
            }

        agg = dict_groups[key]
        pts = row["points"]
        agg["total_points"] += pts
        agg["tasks_attempted"] += 1
        agg["dq_count"] += row["is_dq"]

        if row["task_type"] == "Prize":
            agg["prize_points"] += pts
        elif row["task_type"] == "Normal":
            agg["normal_points"] += pts
        elif row["task_type"] == "Team":
            agg["team_points"] += pts
        elif row["task_type"] == "Live":
            agg["live_points"] += pts

        if row["is_ep_winner"] == 1:
            agg["episodes_won"].add(row["episode_num"])

        if row["is_tiebreaker"] == 1 and row["is_ep_winner"] == 1:
            agg["tiebreak_wins"] += 1

    df_summary = []
    for key, agg in sorted(dict_groups.items()):
        v_series_id, v_contestant_name = key
        df_summary.append({
            "series_id": v_series_id,
            "contestant_name": v_contestant_name,
            "total_points": agg["total_points"],
            "prize_points": agg["prize_points"],
            "normal_points": agg["normal_points"],
            "team_points": agg["team_points"],
            "live_points": agg["live_points"],
            "tasks_attempted": agg["tasks_attempted"],
            "episode_wins": len(agg["episodes_won"]),
            "tiebreak_wins": agg["tiebreak_wins"],
            "dq_count": agg["dq_count"],
        })

    return df_summary


def main():
    v_series_id = 1
    v_polling_delay_sec = 1.0
    df_all_telemetry = []
    os.makedirs("data", exist_ok=True)

    print("Starting Taskmaster Telemetry Ingestion...")

    while True:
        print(f"Fetching Series {v_series_id}...")
        df_tasks, v_eps = parse_series_from_api(v_series_id)

        if not df_tasks or v_eps == 0:
            print(
                f"No tasks found for Series {v_series_id}. Reached series"
                " boundary."
            )
            break

        v_unique_tasks = len(
            set((t["episode_num"], t["task_num"]) for t in df_tasks)
        )
        print(
            f"  > Series {v_series_id} Complete: {v_eps} episodes,"
            f" {v_unique_tasks} unique tasks ({len(df_tasks)} scoring"
            " records)."
        )

        df_all_telemetry.extend(df_tasks)

        if v_series_id > 20 and v_eps < 10:
            print(
                f"\nSeries {v_series_id} is currently active ({v_eps} episodes"
                " aired). Stopping crawl."
            )
            break

        v_series_id += 1
        time.sleep(v_polling_delay_sec)

    # 1. Export Raw Flat JSON
    with open("data/telemetry.json", "w", encoding="utf-8") as f:
        json.dump(df_all_telemetry, f, indent=2, ensure_ascii=False)

    # 2. Export Flat CSV
    if df_all_telemetry:
        v_fieldnames = list(df_all_telemetry[0].keys())
        with open(
            "data/telemetry.csv", "w", newline="", encoding="utf-8-sig"
        ) as f:
            v_writer = csv.DictWriter(f, fieldnames=v_fieldnames)
            v_writer.writeheader()
            v_writer.writerows(df_all_telemetry)

    # 3. Export Summary CSV
    df_summary = build_series_aggregates(df_all_telemetry)
    if df_summary:
        v_summary_fields = list(df_summary[0].keys())
        with open(
            "data/series_summary.csv", "w", newline="", encoding="utf-8-sig"
        ) as f:
            v_writer = csv.DictWriter(f, fieldnames=v_summary_fields)
            v_writer.writeheader()
            v_writer.writerows(df_summary)

    print("\nScrape Finished: Files saved to ./data/")


if __name__ == "__main__":
    main()