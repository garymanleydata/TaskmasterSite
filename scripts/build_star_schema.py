import json
import os


def transform_to_star_schema(
    v_input_path: str = "data/telemetry.json",
    v_output_path: str = "data/star_schema.json",
):
    if not os.path.exists(v_input_path):
        print(f"Error: {v_input_path} not found.")
        return

    with open(v_input_path, "r", encoding="utf-8") as f:
        v_flat_data = json.load(f)

    dict_series = {}
    dict_contestants = {}
    dict_tasks = {}
    fact_task_scores = []

    v_contestant_seq = 1
    v_task_seq = 1
    v_score_seq = 1

    for row in v_flat_data:
        v_s_id = row["series_id"]
        v_ep_num = row["episode_num"]
        v_t_num = row["task_num"]
        v_c_name = row["contestant_name"]

        # Series Dimension
        if v_s_id not in dict_series:
            dict_series[v_s_id] = {"series_id": v_s_id, "episode_count": 0}
        dict_series[v_s_id]["episode_count"] = max(
            dict_series[v_s_id]["episode_count"], v_ep_num
        )

        # Contestant Dimension
        c_key = (v_s_id, v_c_name)
        if c_key not in dict_contestants:
            dict_contestants[c_key] = {
                "contestant_id": v_contestant_seq,
                "series_id": v_s_id,
                "contestant_name": v_c_name,
            }
            v_contestant_seq += 1
        v_c_id = dict_contestants[c_key]["contestant_id"]

        # Task Dimension
        t_key = (v_s_id, v_ep_num, v_t_num, row["task_description"])
        if t_key not in dict_tasks:
            dict_tasks[t_key] = {
                "task_id": v_task_seq,
                "series_id": v_s_id,
                "episode_num": v_ep_num,
                "task_num": v_t_num,
                "task_description": row["task_description"],
                "task_type": row["task_type"],
                "is_tiebreaker": row["is_tiebreaker"],
            }
            v_task_seq += 1
        v_t_id = dict_tasks[t_key]["task_id"]

        # Fact Table
        fact_task_scores.append({
            "score_id": v_score_seq,
            "task_id": v_t_id,
            "contestant_id": v_c_id,
            "series_id": v_s_id,
            "episode_num": v_ep_num,
            "points": row["points"],
            "is_dq": row["is_dq"],
            "is_ep_winner": row["is_ep_winner"],
        })
        v_score_seq += 1

    star_schema_payload = {
        "dim_series": list(dict_series.values()),
        "dim_contestant": list(dict_contestants.values()),
        "dim_task": list(dict_tasks.values()),
        "fact_task_scores": fact_task_scores,
    }

    with open(v_output_path, "w", encoding="utf-8") as f:
        json.dump(star_schema_payload, f, indent=2, ensure_ascii=False)

    print(f"Star Schema Generated: {v_output_path}")


if __name__ == "__main__":
    transform_to_star_schema()