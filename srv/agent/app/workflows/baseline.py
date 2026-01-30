from typing import Any, Dict

from app.clients.busibox import BusiboxClient


async def data_and_enrich(client: BusiboxClient, path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Baseline workflow: ingest a document and trigger enrichment.
    """
    data_result = await client.data_document(path=path, metadata=metadata)
    # Future: trigger additional enrichment steps (embeddings, summaries) via Busibox APIs
    return {"data": data_result}
