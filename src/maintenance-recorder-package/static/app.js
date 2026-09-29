function toggleHistory(historyId) {
    const historyRow = document.getElementById(historyId);

    if (historyRow.style.display === "none") {
        historyRow.style.display = "table-row";
    } else {
        historyRow.style.display = "none";
    }
}