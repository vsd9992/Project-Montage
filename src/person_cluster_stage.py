"""People clustering (idea §7): group detected faces (face_stage.py) into person_id
clusters by embedding similarity, so a later step can let the user label clusters (e.g.
"Person 01" -> "Bride") and idea §7's "prioritize bride and groom" selection logic has
something to key off. Labeling itself is a user-facing UI concern and out of scope here --
this stage only assigns `faces.person_id` and creates `people` rows.

Clustering approach: greedy single-link over cosine similarity. InsightFace's
`buffalo_l` embeddings are already L2-normalizable descriptors where cosine similarity is
the standard match signal; a full clustering library (HDBSCAN/sklearn) is not part of this
project's verified dependency set, so a small dependency-free greedy pass is used instead
-- adequate at the hundreds-of-faces scale this project operates at (792 faces from a
180-photo shortlist), not intended to scale to tens of thousands of faces.
"""

import argparse

import numpy as np

from db import connect

SIMILARITY_THRESHOLD = 0.45  # cosine similarity; InsightFace buffalo_l same-person pairs typically score higher


def _load_embeddings(conn) -> list[tuple[int, np.ndarray]]:
    rows = conn.execute("SELECT id, embedding FROM faces WHERE person_id IS NULL").fetchall()
    out = []
    for face_id, blob in rows:
        vec = np.frombuffer(blob, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        out.append((face_id, vec))
    return out


def cluster_faces(embeddings: list[tuple[int, np.ndarray]], threshold: float = SIMILARITY_THRESHOLD) -> dict[int, int]:
    """Greedy single-link clustering: each face joins the first existing cluster whose
    centroid it's similar enough to, else starts a new one. Returns {face_id: cluster_idx}."""
    clusters: list[dict] = []  # each: {"centroid": np.ndarray, "count": int, "members": [face_id]}
    assignment: dict[int, int] = {}

    for face_id, vec in embeddings:
        best_idx, best_sim = None, -1.0
        for idx, c in enumerate(clusters):
            sim = float(np.dot(vec, c["centroid"]))
            if sim > best_sim:
                best_sim, best_idx = sim, idx
        if best_idx is not None and best_sim >= threshold:
            c = clusters[best_idx]
            c["centroid"] = (c["centroid"] * c["count"] + vec) / (c["count"] + 1)
            c["centroid"] /= np.linalg.norm(c["centroid"])
            c["count"] += 1
            c["members"].append(face_id)
            assignment[face_id] = best_idx
        else:
            clusters.append({"centroid": vec.copy(), "count": 1, "members": [face_id]})
            assignment[face_id] = len(clusters) - 1

    return assignment, clusters


def run(db_path: str, min_cluster_size: int = 1) -> None:
    conn = connect(db_path)
    embeddings = _load_embeddings(conn)
    if not embeddings:
        print("No unclustered faces found.")
        conn.close()
        return

    assignment, clusters = cluster_faces(embeddings)

    kept_clusters = [i for i, c in enumerate(clusters) if c["count"] >= min_cluster_size]
    idx_to_person_id = {}
    for cluster_idx in kept_clusters:
        cur = conn.execute("INSERT INTO people (label) VALUES (NULL)")
        idx_to_person_id[cluster_idx] = cur.lastrowid

    updated = 0
    for face_id, cluster_idx in assignment.items():
        person_id = idx_to_person_id.get(cluster_idx)
        if person_id is None:
            continue  # singleton cluster below min_cluster_size, left unclustered
        conn.execute("UPDATE faces SET person_id = ? WHERE id = ?", (person_id, face_id))
        updated += 1

    conn.commit()

    sizes = sorted((c["count"] for i, c in enumerate(clusters) if i in kept_clusters), reverse=True)
    conn.close()
    print(f"Clustered {updated}/{len(embeddings)} faces into {len(kept_clusters)} people "
          f"(min_cluster_size={min_cluster_size})")
    print(f"Cluster sizes (largest first): {sizes[:15]}{'...' if len(sizes) > 15 else ''}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cluster detected faces into person_id groups")
    parser.add_argument("--db", default="cache/project_full.db")
    parser.add_argument("--min-cluster-size", type=int, default=1,
                         help="Clusters smaller than this are left unassigned (person_id stays NULL)")
    args = parser.parse_args()
    run(args.db, args.min_cluster_size)
