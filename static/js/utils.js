export function getFillPercent(rank, maxRank) {
    if (rank < 1) return 100;
    if (rank > maxRank) return 0;
    if (rank <= 500) {
        return Math.round(
            100 - (rank - 1) * (50 / (500 - 1))
        );
    } else {
        return Math.round(
            50 - (rank - 500) * (50 / (maxRank - 500))
        );
    }
}

export function getBarColor(rank) {
    if (rank <= 300) return "green";
    if (rank <= 750) return "orange";
    return "red";
}
