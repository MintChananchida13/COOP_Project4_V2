// Restore the existing UI contract while mapping each wire candidate only once.
export function createDetectionCandidateResolver<T>(
  pool: unknown,
  map: (candidate: Record<string, unknown>) => T,
) {
  const entries = Array.isArray(pool) ? pool : [];
  const mapped = new Map<number, T>();
  return (candidate: Record<string, unknown>): T => {
    if (!("candidate_ref" in candidate)) return map(candidate);
    const index = candidate.candidate_ref;
    if (typeof index !== "number" || !Number.isInteger(index) || index < 0 ||
        !entries[index] || typeof entries[index] !== "object" || Array.isArray(entries[index])) {
      throw new Error("Invalid detection candidate reference");
    }
    if (!mapped.has(index)) mapped.set(index, map(entries[index] as Record<string, unknown>));
    return mapped.get(index)!;
  };
}
