  //--- START OF FILE App.jsx ---

  import React, { useState, useEffect, useRef } from 'react';
  import axios from 'axios';
  import { 
    FileVideo, ArrowLeft, Play, Download, StopCircle, 
    Layers, CheckCircle, AlertTriangle, X, Trash2 
  } from 'lucide-react';

  const API_BASE = "http://localhost:8000";

  /* =========================================================================
    COMPONENT: CLIP CARD
    Displays video, status, and actions for a single clip.
    ========================================================================= */
  const ClipCard = React.memo(({ clip, openMaskModal, currentProject, status }) => {
    const videoSrc = `${API_BASE}/inputs/${clip.path.replace(/^\.\/videos\//, "")}`;
    const outputSrc = `${API_BASE}/outputs/${currentProject}/${clip.clip_id}.mp4`;
    
    const isProcessing = status.current_clip === clip.clip_id;
    
    // Cache buster for output video to ensure refresh
    const [cacheBuster, setCacheBuster] = useState(Date.now());

    useEffect(() => {
      if (clip.status === "done") {
        setCacheBuster(Date.now());
      }
    }, [clip.status]);

    const isNoChar = clip.type === "NoChar";

    return (
      <div className={`bg-gray-800 p-4 rounded-lg shadow-lg border ${isProcessing ? "border-yellow-500" : "border-gray-700"}`}>
        
        {/* Header: ID and Status */}
        <div className="flex justify-between items-center mb-2">
          <span className="font-bold text-gray-300 text-sm truncate max-w-[150px]" title={clip.clip_id}>{clip.clip_id}</span>
          {isProcessing ? (
            <span className="text-yellow-400 text-xs animate-pulse">Processing Pass {status.current_pass}...</span>
          ) : clip.status === "done" ? (
            <span className="text-green-400 text-xs flex items-center gap-1"><CheckCircle size={12}/> Done</span>
          ) : (
            <span className="text-gray-500 text-xs">{isNoChar ? "Source Only" : "Pending"}</span>
          )}
        </div>

        {/* Input Video */}
        <div className="relative mb-2 bg-black rounded overflow-hidden aspect-video">
          <video src={videoSrc} controls className="w-full h-full object-contain" />
          <span className="absolute top-1 left-1 bg-black/50 text-white text-[10px] px-1 rounded">Input</span>
        </div>

        {/* Output Video (if exists) */}
        {clip.status === "done" && (
          <div className="relative mb-3 bg-black rounded overflow-hidden aspect-video border border-green-500/30">
            <video src={`${outputSrc}?t=${cacheBuster}`} controls className="w-full h-full object-contain" />
            <span className="absolute top-1 left-1 bg-green-900/70 text-white text-[10px] px-1 rounded">Output</span>
          </div>
        )}

        {/* Actions (Hidden for NoChar clips) */}
        {!isNoChar && (
          <div className="flex gap-2 mt-auto">
            <button
              onClick={() => openMaskModal(clip)}
              className="flex-1 bg-blue-600 hover:bg-blue-500 text-white py-2 rounded text-sm transition"
            >
              {clip.status === "done" ? "Regenerate" : "Generate"}
            </button>
            
            {clip.status === "done" && (
              <a
                href={outputSrc}
                download={`${clip.clip_id}.mp4`}
                className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center justify-center"
                title="Download Clip"
              >
                <Download size={16} />
              </a>
            )}
          </div>
        )}
        
        {/* Info for NoChar */}
        {isNoChar && (
          <div className="mt-auto p-2 bg-gray-700/50 rounded text-center text-xs text-gray-400">
            No processing required
          </div>
        )}
      </div>
    );
  });

  /* =========================================================================
    MAIN APP COMPONENT
    ========================================================================= */
  function App() {
    const [projects, setProjects] = useState([]);
    const [currentProject, setCurrentProject] = useState(null);
    const [jobData, setJobData] = useState(null);
    const [status, setStatus] = useState({ is_processing: false, queue: [], last_completed: null });
    
    // Custom Char State
    const [char1, setChar1] = useState(null);
    const [char2, setChar2] = useState(null);
    const [hasCustomChars, setHasCustomChars] = useState({ char1: false, char2: false });

    // Modal State
    const [activeClip, setActiveClip] = useState(null);
    const [frameUrl, setFrameUrl] = useState(null);
    const [maskPoints, setMaskPoints] = useState({ positive: [], negative: [] });
    const [frameIndex, setFrameIndex] = useState(0);
    const [modalTab, setModalTab] = useState(1); // Pass 1 or 2

    // Alert State
    const lastAlertedJob = useRef(null);

    // Effects
    useEffect(() => {
      fetchProjects();
      checkCustomChars();
      
      // Combined Polling: Status + Project Data
      const interval = setInterval(() => {
        fetchStatusAndProject();
      }, 1000);
      
      return () => clearInterval(interval);
    }, [currentProject]);

    const fetchStatusAndProject = async () => {
      // 1. Fetch Global Status
      try {
        const res = await axios.get(`${API_BASE}/status`);
        const newStatus = res.data;
        
        setStatus(prev => {
          if (JSON.stringify(prev) !== JSON.stringify(newStatus)) return newStatus;
          return prev;
        });

        // CHECK FOR COMPLETION ALERT
        if (newStatus.last_completed && newStatus.last_completed !== lastAlertedJob.current) {
          // It's a new completed job
          alert(`Job Completed: ${newStatus.last_completed}`);
          lastAlertedJob.current = newStatus.last_completed;
        }

      } catch(e) {}

      // 2. Fetch Project Data (if loaded)
      if (currentProject) {
        try {
          const res = await axios.get(`${API_BASE}/project/${currentProject}`);
          setJobData(prev => {
            if (JSON.stringify(prev) !== JSON.stringify(res.data)) return res.data;
            return prev;
          });
        } catch(e) {}
      }
    };

    const fetchProjects = async () => {
      const res = await axios.get(`${API_BASE}/projects`);
      setProjects(res.data);
    };

    const checkCustomChars = async () => {
      const res = await axios.get(`${API_BASE}/characters/check`);
      setHasCustomChars(res.data);
      if(res.data.char1) setChar1(`${API_BASE}/assets/custom_char1.png`);
      if(res.data.char2) setChar2(`${API_BASE}/assets/custom_char2.png`);
    };

    const loadProject = async (id) => {
      if (!hasCustomChars.char1 || !hasCustomChars.char2) {
        if (!window.confirm("Warning: Custom characters are not fully uploaded. The system will use default placeholders. Continue?")) {
          return;
        }
      }
      setCurrentProject(id);
      const res = await axios.get(`${API_BASE}/project/${id}`);
      setJobData(res.data);
    };

    // -------------------------------------------------------------------------
    // ACTION HANDLERS
    // -------------------------------------------------------------------------

    const handleUploadChar = async (name, file) => {
      const formData = new FormData();
      formData.append("file", file);
      const res = await axios.post(`${API_BASE}/character/upload/${name}`, formData);
      if (name === "char1") setChar1(`${API_BASE}${res.data.url}`);
      else setChar2(`${API_BASE}${res.data.url}`);
      checkCustomChars();
    };

    const openMaskModal = (clip) => {
      setActiveClip(clip);
      setModalTab(1); // Start at Pass 1
      setFrameIndex(0);
      loadFrame(clip, 0);
      setMaskPoints({ positive: [], negative: [] });
      loadMaskForPass(clip, 1);
    };

    const loadFrame = async (clip, index) => {
      setFrameUrl(null); 
      const res = await axios.get(`${API_BASE}/frame/${currentProject}/${clip.clip_id}?frame=${index}`);
      setFrameUrl(`${API_BASE}${res.data.url}?t=${Date.now()}`);
    };

    const loadMaskForPass = async (clip, pass) => {
      try {
        const res = await axios.get(`${API_BASE}/mask/load/${currentProject}/${clip.clip_id}/${pass}`);
        setMaskPoints(res.data);
      } catch (e) {
        console.log("No existing mask found, starting fresh.");
        setMaskPoints({ positive: [], negative: [] });
      }
    };

    const saveMaskCurrentPass = async () => {
      if (!activeClip) return;
      try {
        await axios.post(
          `${API_BASE}/mask/save/${currentProject}/${activeClip.clip_id}/${modalTab}`,
          maskPoints
        );
      } catch (e) {
        alert("Failed to save mask.");
        throw e;
      }
    };

    const handleTabChange = async (newPass) => {
      try {
        await saveMaskCurrentPass();
        setModalTab(newPass);
        await loadMaskForPass(activeClip, newPass);
      } catch (e) {}
    };

    const confirmAndGenerateSingle = async () => {
      try {
        await saveMaskCurrentPass();
        await axios.post(`${API_BASE}/queue/clip/${currentProject}`, activeClip);
        setActiveClip(null);
        alert("Job queued successfully!");
      } catch (e) {
        console.error(e);
        alert("Error queueing job. Check console.");
      }
    };

    const handleQueueAll = async () => {
      try {
        const res = await axios.post(`${API_BASE}/queue/all/${currentProject}`);
        if (res.data.status === "error") {
          alert("Cannot queue: \n" + res.data.message + "\n\nMissing: " + res.data.missing.join(", "));
        } else {
          alert(`Successfully queued ${res.data.count} clips.`);
        }
      } catch (e) {
        alert("Error queuing clips.");
      }
    };

    const handleStop = async () => {
      await axios.post(`${API_BASE}/stop`);
      alert("Stop signal sent.");
    };

    const handleReset = async () => {
      if (window.confirm("Are you sure you want to reset this project? This will delete all generated deepfake clips. Masks will be preserved.")) {
        try {
          await axios.post(`${API_BASE}/reset/${currentProject}`);
          alert("Project reset. Clips cleared.");
          loadProject(currentProject);
        } catch(e) {
          alert("Reset failed.");
        }
      }
    };

    const handleStitch = async () => {
      if (!jobData || !jobData.clips) return;

      const missingClips = jobData.clips.filter(c => c.type !== "NoChar" && c.status !== "done");

      if (missingClips.length > 0) {
        const confirmMsg = `Warning: ${missingClips.length} clips have not been generated yet.\n` + 
                          `Original clips will be used for these segments.\n` +
                          `Do you want to continue stitching?`;
        if (!window.confirm(confirmMsg)) return;
      }

      try {
        const res = await axios.post(`${API_BASE}/stitch/${currentProject}`);
        const link = document.createElement('a');
        link.href = `${API_BASE}${res.data.url}`;
        link.download = `${currentProject}_final.mp4`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      } catch (e) {
        console.error(e);
        alert("Stitching failed. Check backend console.");
      }
    };

    // -------------------------------------------------------------------------
    // RENDER
    // -------------------------------------------------------------------------
    if (!currentProject) {
      return (
        <div className="min-h-screen bg-gray-900 text-white p-10 font-sans">
          <h1 className="text-4xl font-bold mb-8 text-blue-400 text-center">DeepFake Studio</h1>
          <div className="max-w-4xl mx-auto bg-gray-800 p-8 rounded-xl mb-12 shadow-lg">
            <h2 className="text-2xl font-semibold mb-6 flex items-center gap-2">
              <Layers /> Character Setup
            </h2>
            <div className="flex gap-10 justify-center">
              {['char1', 'char2'].map(char => (
                <div key={char} className="flex flex-col items-center">
                  <div className="w-40 h-40 bg-gray-700 rounded-lg mb-4 overflow-hidden border-2 border-dashed border-gray-500 flex items-center justify-center relative group">
                    {char === 'char1' ? char1 ? <img src={char1} className="w-full h-full object-cover" /> : <span className="text-gray-400">No Image</span> : char2 ? <img src={char2} className="w-full h-full object-cover" /> : <span className="text-gray-400">No Image</span>}
                  </div>
                  <label className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded cursor-pointer">
                    Upload {char === 'char1' ? 'Char 1' : 'Char 2'}
                    <input type="file" className="hidden" accept="image/*" onChange={(e) => handleUploadChar(char, e.target.files[0])} />
                  </label>
                </div>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-4xl mx-auto">
            {projects.map(p => (
              <button key={p} onClick={() => loadProject(p)} className="bg-gray-800 hover:bg-gray-700 p-8 rounded-xl transition shadow-md flex flex-col items-center gap-4 group">
                <FileVideo className="w-12 h-12 text-blue-500 group-hover:scale-110 transition" />
                <span className="text-lg font-medium">{p}</span>
              </button>
            ))}
          </div>
        </div>
      );
    }

    return (
      <div className="min-h-screen bg-gray-900 text-white p-6 pb-24 font-sans relative">
        <div className="flex justify-between items-center mb-8 bg-gray-800 p-4 rounded-lg shadow-md">
          <div className="flex items-center gap-4">
            <button onClick={() => setCurrentProject(null)} className="flex items-center gap-2 text-gray-400 hover:text-white transition">
              <ArrowLeft size={20} /> Back
            </button>
            <h1 className="text-xl font-bold text-blue-400 border-l border-gray-600 pl-4">{currentProject}</h1>
          </div>
          <div className="flex gap-3">
            <button onClick={handleReset} className="bg-red-900/50 hover:bg-red-800 text-red-200 px-4 py-2 rounded flex items-center gap-2 border border-red-800">
              <Trash2 size={18} /> Reset
            </button>
            {status.is_processing ? (
              <button onClick={handleStop} className="bg-red-600 hover:bg-red-500 px-4 py-2 rounded flex items-center gap-2 animate-pulse">
                <StopCircle size={18} /> Stop Generation
              </button>
            ) : (
              <button onClick={handleQueueAll} className="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded flex items-center gap-2">
                <Play size={18} /> Queue Entire Video
              </button>
            )}
            <button onClick={handleStitch} className="bg-green-600 hover:bg-green-500 px-4 py-2 rounded flex items-center gap-2">
              <Download size={18} /> Stitch & Download
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {jobData?.clips.map((clip) => (
            <ClipCard
              key={clip.clip_id}
              clip={clip}
              openMaskModal={openMaskModal}
              currentProject={currentProject}
              status={status}
            />
          ))}
        </div>

        {activeClip && (
          <MaskModal 
            clip={activeClip}
            frameUrl={frameUrl}
            setFrameUrl={setFrameUrl}
            frameIndex={frameIndex}
            setFrameIndex={setFrameIndex}
            maskPoints={maskPoints}
            setMaskPoints={setMaskPoints}
            modalTab={modalTab}
            onTabChange={handleTabChange}
            onSave={saveMaskCurrentPass}
            onConfirm={confirmAndGenerateSingle}
            onClose={() => setActiveClip(null)}
            loadFrame={loadFrame}
            loadLastMask={() => loadMaskForPass(activeClip, modalTab)}
            currentProject={currentProject}
          />
        )}
      </div>
    );
  }

  /* =========================================================================
    COMPONENT: MASK MODAL
    ========================================================================= */
  const MaskModal = ({ 
    clip, frameUrl, setFrameUrl, frameIndex, setFrameIndex,
    maskPoints, setMaskPoints, modalTab, onTabChange,
    onSave, onConfirm, onClose, loadFrame, loadLastMask, currentProject 
  }) => {
    const actions = clip.actions || [];
    const isMultiPass = actions.length > 1;
    const currentAction = actions.find(a => a.pass === modalTab);
    const resolution = (currentProject === "Video2") ? { width: 480, height: 832 } : { width: 832, height: 480 };

    return (
      <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 backdrop-blur-sm">
        <div className="bg-gray-800 p-6 rounded-xl w-[1000px] max-h-[95vh] overflow-y-auto relative shadow-2xl border border-gray-700">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-2xl font-bold text-white">Masking: {clip.clip_id}</h2>
            <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl">&times;</button>
          </div>
          {isMultiPass && (
            <div className="flex gap-2 mb-4 border-b border-gray-600 pb-2">
              {actions.map(action => (
                <button key={action.pass} onClick={() => onTabChange(action.pass)} className={`px-4 py-2 rounded-t-lg transition ${modalTab === action.pass ? "bg-blue-600 text-white font-bold" : "bg-gray-700 text-gray-400 hover:bg-gray-600"}`}>
                  Pass {action.pass}: {action.character}
                </button>
              ))}
            </div>
          )}
          <div className="bg-gray-700/50 p-3 rounded mb-4 text-sm text-gray-300 flex justify-between items-center">
            <span>Target Character: <strong className="text-blue-300">{currentAction?.character || "N/A"}</strong></span>
            <span className="flex gap-4">
              <span className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-lime-500"></div> Left Click (Positive)</span>
              <span className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-red-500"></div> Shift + Click (Negative)</span>
            </span>
          </div>
          <div className="flex gap-3 mb-4">
            <button onClick={() => { setFrameIndex(0); loadFrame(clip, 0); }} className="bg-gray-600 hover:bg-gray-500 px-3 py-1 rounded text-sm">First Frame</button>
            <button onClick={() => { const f = prompt("Frame Number:"); if(f) { setFrameIndex(parseInt(f)); loadFrame(clip, parseInt(f)); }}} className="bg-purple-600 hover:bg-purple-500 px-3 py-1 rounded text-sm">Custom Frame</button>
            <div className="flex-grow"></div>
            <button onClick={loadLastMask} className="bg-yellow-600 hover:bg-yellow-500 px-3 py-1 rounded text-sm">Reload Saved Mask</button>
            <button onClick={onSave} className="bg-orange-600 hover:bg-orange-500 px-3 py-1 rounded text-sm">Save Progress</button>
          </div>
          <div className="bg-black/40 rounded flex justify-center p-4 min-h-[400px]">
            {frameUrl ? (
              <MaskCanvas imageUrl={frameUrl} resolution={resolution} points={maskPoints} onChange={setMaskPoints} />
            ) : (
              <div className="flex items-center text-gray-500">Loading frame...</div>
            )}
          </div>
          <div className="flex justify-end gap-4 mt-6">
            <button onClick={onClose} className="bg-gray-600 hover:bg-gray-500 px-6 py-2 rounded">Cancel</button>
            <button onClick={onConfirm} className="bg-green-600 hover:bg-green-500 px-6 py-2 rounded font-bold shadow-lg shadow-green-900/20">Confirm & Queue</button>
          </div>
        </div>
      </div>
    );
  };

  /* =========================================================================
    COMPONENT: MASK CANVAS
    ========================================================================= */
  function MaskCanvas({ imageUrl, resolution, points, onChange }) {
    const canvasRef = useRef(null);
    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "lime";
      points.positive.forEach(p => { ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, 2 * Math.PI); ctx.fill(); });
      ctx.fillStyle = "red";
      points.negative.forEach(p => { ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, 2 * Math.PI); ctx.fill(); });
    }, [points]);

    const handleClick = (e) => {
      const rect = canvasRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      if (e.shiftKey) {
        onChange(prev => ({ ...prev, negative: [...prev.negative, { x, y }] }));
      } else {
        onChange(prev => ({ ...prev, positive: [...prev.positive, { x, y }] }));
      }
    };

    return (
      <div style={{ position: "relative", width: resolution.width, height: resolution.height }}>
        <img src={imageUrl} alt="frame" style={{ width: resolution.width, height: resolution.height }} className="select-none" />
        <canvas ref={canvasRef} width={resolution.width} height={resolution.height} onClick={handleClick} onContextMenu={(e) => e.preventDefault()} style={{ position: "absolute", top: 0, left: 0, cursor: "crosshair" }} />
      </div>
    );
  }

  export default App;