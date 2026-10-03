"""Bounded local search over the installed-app catalog and Windows' own index."""
from __future__ import annotations

from dataclasses import dataclass
import time

from .apps import installed_apps


@dataclass(frozen=True)
class SearchResult:
    name: str
    path: str
    kind: str


class DesktopSearch:
    """One UI-owned catalog cache. No disk crawler, watcher, or persistent state."""

    def __init__(self) -> None:
        self._apps = []
        self._catalog_at = 0.0

    def query(self, text: str) -> tuple[list[SearchResult], str]:
        query = str(text).strip()[:200]
        if not query:
            return [], ""
        now = time.monotonic()
        if now-self._catalog_at > 60:
            self._apps = installed_apps()
            self._catalog_at = now
        results = [SearchResult(app.name, app.path, "App") for app in self._apps
                   if query.casefold() in app.name.casefold()][:20]
        note = ""
        try:
            indexed = indexed_results(query)
        except Exception:
            indexed = []
            note = "Windows file index unavailable · Apps only"
        seen = {result.path.casefold() for result in results}
        for result in indexed:
            if result.path.casefold() not in seen:
                results.append(result)
                seen.add(result.path.casefold())
        return results[:40], note


def indexed_results(query: str) -> list[SearchResult]:
    """Query the local SystemIndex; indexing coverage remains Windows-owned."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    connection = recordset = None
    try:
        connection = win32com.client.Dispatch("ADODB.Connection")
        connection.ConnectionTimeout = 2
        connection.CommandTimeout = 2
        connection.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        # Only literal filename text enters SQL; quotes and wildcard characters
        # cannot become syntax or silently broaden an operator's query.
        literal = query.replace("'", "''").replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
        sql = ("SELECT TOP 30 System.ItemNameDisplay, System.ItemPathDisplay FROM SYSTEMINDEX "
               "WHERE SCOPE='file:' AND System.FileName LIKE '%"+literal+"%' ORDER BY System.DateModified DESC")
        recordset, _ = connection.Execute(sql)
        rows = []
        while not recordset.EOF and len(rows) < 30:
            name, path = (recordset.Fields.Item(i).Value for i in range(2))
            if name and path:
                rows.append(SearchResult(str(name), str(path), "File"))
            recordset.MoveNext()
        return rows
    finally:
        if recordset is not None:
            recordset.Close()
        if connection is not None and connection.State:
            connection.Close()
        pythoncom.CoUninitialize()
