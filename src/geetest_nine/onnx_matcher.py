"""ONNX-only nine-grid matcher (SigLIP backbone + spatial-correlation head).

Training contract:
- RGBA prompt alpha-composited on WHITE
- 3x3 row-major cell crops from grid (typically 260x160 → each cell ~86x53)
- 256x256 bilinear resize, ImageNet norm (mean/std)
- Cosine-similarity head returns 9 logits per (prompt, cell) pair

Calibration (401 val grids, threshold 2.2): live 96.7% effective success.

Model artifacts loaded from ``geetest_nine/models/`` unless overridden via
``GEETEST_ONNX_DIR`` env var.
"""

from __future__ import annotations

import io
import os
from importlib import resources

import numpy as np

ONNX_MARGIN_THRESHOLD = 2.2
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_sessions: tuple | None = None


def _model_dir() -> str:
    env = os.environ.get("GEETEST_ONNX_DIR")
    if env:
        return env
    with resources.as_file(resources.files("geetest_nine") / "models") as p:
        return str(p)


def sessions():
    """Lazy-init (backbone, match_head) ONNX sessions. Singleton per process."""
    global _sessions
    if _sessions is None:
        import onnxruntime as ort

        d = _model_dir()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        _sessions = (
            ort.InferenceSession(
                os.path.join(d, "vision_backbone.onnx"),
                so,
                providers=["CPUExecutionProvider"],
            ),
            ort.InferenceSession(
                os.path.join(d, "match_head.onnx"),
                so,
                providers=["CPUExecutionProvider"],
            ),
        )
    return _sessions


class OnnxMatcher:
    """Local nine-grid matcher. ``solve(grid_bytes, prompt_bytes)`` → (indices, margin)."""

    @classmethod
    def _prep(cls, pil_img) -> np.ndarray:
        from PIL import Image as _Image

        img = pil_img.convert("RGBA") if pil_img.mode in ("RGBA", "LA") else pil_img
        if img.mode == "RGBA":
            bg = _Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        else:
            img = img.convert("RGB")
        x = np.asarray(img.resize((256, 256), _Image.BILINEAR), dtype=np.float32) / 255.0
        x = (x - _MEAN) / _STD
        return x.transpose(2, 0, 1)[None]

    @classmethod
    def solve(cls, img_bytes: bytes, prompt_bytes: bytes):
        """Return ``(top3_indices, margin)`` or ``None`` on any error.

        ``top3_indices`` are 0-based row-major indices of the 3x3 grid.
        ``margin = logits[2] - logits[3]`` — larger means more confident.
        """
        try:
            from PIL import Image as _Image

            bb, head = sessions()
            grid = _Image.open(io.BytesIO(img_bytes)).convert("RGB")
            prompt = _Image.open(io.BytesIO(prompt_bytes))  # keep alpha
            w, h = grid.size
            cw, ch = w / 3.0, h / 3.0
            cells = [
                grid.crop((int(c * cw), int(r * ch), int((c + 1) * cw), int((r + 1) * ch)))
                for r in range(3)
                for c in range(3)
            ]
            f_p = bb.run(["features"], {"image": cls._prep(prompt).astype(np.float32)})[0]
            f_c = bb.run(
                ["features"],
                {"image": np.concatenate([cls._prep(c).astype(np.float32) for c in cells], 0)},
            )[0]
            f_p9 = np.repeat(f_p, 9, axis=0)
            logits = head.run(
                ["logits"], {"prompt_features": f_p9, "cell_features": f_c}
            )[0]
            order = np.argsort(-logits)
            top3 = sorted(order[:3].tolist())
            margin = float(logits[order[2]] - logits[order[3]])
            return top3, margin
        except Exception as e:  # noqa: BLE001
            print(f"  [onnx] matcher error: {e}")
            return None


class OnnxLowConfidence(Exception):
    """ONNX margin < threshold. Carries best-guess indices for fallback."""

    def __init__(self, reason: str, indices):
        super().__init__(reason)
        self.indices = indices
