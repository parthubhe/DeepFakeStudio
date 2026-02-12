import React, { useRef, useEffect, useState } from "react";

export default function PointsCanvas({
  videoId,
  clipId,
  imageUrl,
  onSave,
  onClose
}) {
  const canvasRef = useRef(null);
  const imgRef = useRef(null);

  const [points, setPoints] = useState({
    positive: [],
    negative: []
  });

  // ============================================
  // Resolution logic (must match backend)
  // ============================================

  let realWidth = 832;
  let realHeight = 480;

  if (videoId === "Video2") {
    realWidth = 480;
    realHeight = 832;
  }

  // ============================================
  // Draw everything
  // ============================================

  const redraw = () => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");

    ctx.clearRect(0, 0, realWidth, realHeight);

    if (imgRef.current) {
      ctx.drawImage(imgRef.current, 0, 0, realWidth, realHeight);
    }

    // Positive = green
    ctx.fillStyle = "#22c55e";
    points.positive.forEach(p => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 6, 0, 2 * Math.PI);
      ctx.fill();
    });

    // Negative = red
    ctx.fillStyle = "#ef4444";
    points.negative.forEach(p => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 6, 0, 2 * Math.PI);
      ctx.fill();
    });
  };

  // ============================================
  // Setup canvas
  // ============================================

  useEffect(() => {
    const canvas = canvasRef.current;

    const displayWidth = 500;
    const scale = displayWidth / realWidth;

    canvas.width = realWidth;
    canvas.height = realHeight;

    canvas.style.width = `${displayWidth}px`;
    canvas.style.height = `${realHeight * scale}px`;

    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = imageUrl;

    img.onload = () => {
      imgRef.current = img;
      redraw();
    };

  }, [imageUrl]);

  useEffect(() => {
    redraw();
  }, [points]);

  // ============================================
  // Mouse handler (CORRECT WAY)
  // ============================================

  const handleMouseDown = (e) => {
    e.preventDefault(); // disable right-click menu

    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();

    const scaleX = realWidth / rect.width;
    const scaleY = realHeight / rect.height;

    const x = (e.clientX - rect.left) * scaleX;
    const y = (e.clientY - rect.top) * scaleY;

    const newPoint = { x, y };

    // Left click = button 0
    if (e.button === 0 && !e.shiftKey) {
      setPoints(prev => ({
        ...prev,
        positive: [...prev.positive, newPoint]
      }));
    }

    // Shift + Right Click = button 2
    if (e.button === 2 && e.shiftKey) {
      setPoints(prev => ({
        ...prev,
        negative: [...prev.negative, newPoint]
      }));
    }
  };

  // Disable default context menu
  const disableContextMenu = (e) => {
    e.preventDefault();
  };

  const clearPoints = () => {
    setPoints({ positive: [], negative: [] });
  };

  const savePoints = () => {
    console.log("Saving mask points:", points);
    onSave(points);
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-slate-800 p-6 rounded-xl w-[700px] shadow-2xl border border-slate-700">

        <div className="flex justify-between items-center mb-4">
          <h2 className="text-white text-lg font-semibold">
            Define Points for {clipId}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xl">
            ×
          </button>
        </div>

        <div className="text-sm text-gray-400 mb-2">
          Left Click = Positive (Green) | Shift + Right Click = Negative (Red)
        </div>

        <div className="flex justify-center mb-4">
          <canvas
            ref={canvasRef}
            onMouseDown={handleMouseDown}
            onContextMenu={disableContextMenu}
            className="border border-slate-600 rounded-md cursor-crosshair"
          />
        </div>

        <div className="text-xs text-gray-400 mb-4">
          Positive: {points.positive.length} | Negative: {points.negative.length}
        </div>

        <div className="flex justify-end gap-4">
          <button
            onClick={clearPoints}
            className="px-4 py-2 bg-gray-600 hover:bg-gray-500 rounded text-white"
          >
            Clear
          </button>

          <button
            onClick={onClose}
            className="px-4 py-2 bg-gray-600 hover:bg-gray-500 rounded text-white"
          >
            Cancel
          </button>

          <button
            onClick={savePoints}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded text-white"
          >
            Confirm & Generate
          </button>
        </div>

      </div>
    </div>
  );
}
