#!/usr/bin/env python3
"""
ColPali Visual Embedding Server

Provides OpenAI-compatible embeddings API for PDF page images using ColPali v1.3.
ColPali generates multi-vector embeddings (128 patches x 128 dims) for visual search.

Reference: https://huggingface.co/vidore/colpali-v1.3
"""

import argparse
import base64
import io
import os
from typing import List, Optional

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

# Import ColPali
try:
    from colpali_engine.models import ColPali, ColPaliProcessor
except ImportError:
    print("ERROR: colpali-engine not installed. Run: pip install colpali-engine>=0.3.0,<0.4.0")
    exit(1)


# Request/Response Models
class EmbeddingRequest(BaseModel):
    """Request model for embeddings."""
    input: List[str] = Field(..., description="List of base64-encoded images or text queries")
    model: str = Field(default="colpali", description="Model name")
    encoding_format: str = Field(default="float", description="Encoding format (float only)")


class EmbeddingData(BaseModel):
    """Single embedding result."""
    object: str = "embedding"
    embedding: List[float]
    index: int


class EmbeddingResponse(BaseModel):
    """Response model for embeddings."""
    object: str = "list"
    data: List[EmbeddingData]
    model: str
    usage: dict


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    model: str
    device: str


# Initialize FastAPI app
app = FastAPI(
    title="ColPali Visual Embedding Server",
    description="OpenAI-compatible embeddings API for PDF page images",
    version="1.0.0",
)

# Global model and processor
model: Optional[ColPali] = None
processor: Optional[ColPaliProcessor] = None
device: str = "cuda:0"
model_name: str = "vidore/colpali-v1.3"


def load_model(model_path: str, device_name: str, dtype: str = "bfloat16"):
    """Load ColPali model and processor."""
    global model, processor, device, model_name
    
    device = device_name
    model_name = model_path
    
    print(f"Loading ColPali model: {model_name}")
    print(f"Device: {device}")
    print(f"Dtype: {dtype}")
    
    # Determine torch dtype
    torch_dtype = torch.bfloat16 if dtype == "bfloat16" else torch.float16
    
    # Load model
    model = ColPali.from_pretrained(
        model_name,
        torch_dtype=torch_dtype,
        device_map=device,
    ).eval()
    
    # Load processor (use fast processor for better performance)
    processor = ColPaliProcessor.from_pretrained(model_name, use_fast=True)
    
    print(f"✓ ColPali model loaded successfully")


def decode_base64_image(base64_str: str) -> Image.Image:
    """Decode base64 string to PIL Image."""
    # Remove data URL prefix if present
    if base64_str.startswith("data:"):
        base64_str = base64_str.split(",", 1)[1]
    
    image_data = base64.b64decode(base64_str)
    return Image.open(io.BytesIO(image_data))


@app.get("/health")
async def health():
    """Health check endpoint."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    return HealthResponse(
        status="healthy",
        model=model_name,
        device=device,
    )


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(request: EmbeddingRequest):
    """
    Generate ColPali embeddings for images or text queries.
    
    Images should be base64-encoded. Text queries are processed as-is.
    Returns flattened embeddings (128 patches * 128 dims = 16384 dims per image).
    """
    if model is None or processor is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    if not request.input:
        raise HTTPException(status_code=400, detail="No input provided")
    
    try:
        # Determine if inputs are images or text queries
        # Images start with base64 encoding or data URL
        is_image = request.input[0].startswith("data:") or len(request.input[0]) > 1000
        
        if is_image:
            # Process images
            images = [decode_base64_image(img_str) for img_str in request.input]
            batch_inputs = processor.process_images(images).to(model.device)
        else:
            # Process text queries
            batch_inputs = processor.process_queries(request.input).to(model.device)
        
        # Generate embeddings
        with torch.no_grad():
            embeddings = model(**batch_inputs)
        
        # Convert to list and flatten multi-vector embeddings
        # ColPali returns (batch, 128 patches, 128 dims)
        # Flatten to (batch, 16384) for compatibility
        embeddings_list = []
        for emb in embeddings:
            # Flatten the multi-vector embedding
            flat_emb = emb.reshape(-1).cpu().float().tolist()
            embeddings_list.append(flat_emb)
        
        # Build response
        data = [
            EmbeddingData(
                embedding=emb,
                index=idx,
            )
            for idx, emb in enumerate(embeddings_list)
        ]
        
        return EmbeddingResponse(
            data=data,
            model=request.model,
            usage={
                "prompt_tokens": len(request.input),
                "total_tokens": len(request.input),
            },
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding generation failed: {str(e)}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="ColPali Visual Embedding Server")
    parser.add_argument("--model", default="vidore/colpali-v1.3", help="Model name or path")
    parser.add_argument("--device", default="cuda:0", help="Device (cuda:0, cuda:1, etc)")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16"], help="Data type")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8002, help="Port to bind to")
    args = parser.parse_args()
    
    # Load model
    load_model(args.model, args.device, args.dtype)
    
    # Start server
    print(f"\nStarting ColPali server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

