//--- START OF FILE App.jsx ---

import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { 
  FileVideo, ArrowLeft, Play, Download, StopCircle, 
  Layers, CheckCircle, AlertTriangle, X, Trash2, Info 
} from 'lucide-react';

// Dynamic API Base for Cloud/Local
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const Toast = ({ message, onClose }) => {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);

  return (
    <div className="fixed bottom-5 right-5 bg-green-600 text-white px-6 py-3 rounded-lg shadow-2xl flex items-center gap-3 animate-bounce z-50">
      <CheckCircle size={24} />
      <div>
        <h4 className="font-bold">System Update</h4>
        <p className="text-sm">{message}</p>
      </div>
      <button onClick={onClose} className="ml-4 hover:bg-green-700 p-1 rounded"><X size={16}/></button>
    </div>
  );
};

const ClipCard = React.memo(({ clip, openMaskModal, currentProject, status }) => {
  const relPath = clip.path.replace(/^\.\/videos\//, "");
  const inputUrl = `${API_BASE}/inputs/${relPath}`;
  const outputSrc = `${API_BASE}/outputs/${currentProject}/${clip.clip_id}.mp4`;
  
  const isProcessing = status.current_clip === clip.clip_id;
  const [cacheBuster, setCacheBuster] = useState(Date.now());

  useEffect(() => {
    if (clip.status === "done") {
      setCacheBuster(Date.now());
    }
  }, [clip.status]);

  const isNoChar = clip.type === "NoChar";

  return (
    <div className={`bg-gray-800 p-4 rounded-lg shadow-lg border ${isProcessing ? "border-yellow-500 shadow-yellow-500/20" : "border-gray-700"}`}>
      <div className="flex justify-between items-center mb-2">
        <span className="font-bold text-gray-300 text-sm truncate max-w-[150px]" title={clip.clip_id}>{clip.clip_id}</span>
        {isProcessing ? (
          <span className="text-yellow-400 text-xs font-mono animate-pulse flex items-center gap-1">
             <div className="w-2 h-2 bg-yellow-400 rounded-full animate-ping"></div>
             Pass {status.current_pass}
          </span>
        ) : clip.status === "done" ? (
          <span className="text-green-400 text-xs flex items-center gap-1"><CheckCircle size={12}/> Done</span>
        ) : (
          <span className="text-gray-500 text-xs">{isNoChar ? "Source Only" : "Pending"}</span>
        )}
      </div>

      <div className="relative mb-2 bg-black rounded overflow-hidden aspect-video group">
        <video src={inputUrl} controls className="w-full h-full object-contain" />
        <span className="absolute top-1 left-1 bg-black/50 text-white text-[10px] px-1 rounded backdrop-blur-sm">Input</span>
      </div>

      {clip.status === "done" && (
        <div className="relative mb-3 bg-black rounded overflow-hidden aspect-video border border-green-500/30">
          <video src={`${outputSrc}?t=${cacheBuster}`} controls className="w-full h-full object-contain" />
          <span className="absolute top-1 left-1 bg-green-900/70 text-white text-[10px] px-1 rounded backdrop-blur-sm">Output</span>
        </div>
      )}

      {!isNoChar && (
        <div className="flex gap-2 mt-auto">
          <button onClick={() => openMaskModal(clip)} className="flex-1 bg-blue-600 hover:bg-blue-500 text-white py-2 rounded text-sm transition">
            {clip.status === "done" ? "Regenerate" : "Generate"}
          </button>
          {clip.status === "done" && (
            <a href={outputSrc} download className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center justify-center">
              <Download size={16} />
            </a>
          )}
        </div>
      )}
      
      {isNoChar && (
        <div className="mt-auto p-2 bg-gray-700/50 rounded text-center text-xs text-gray-400">
          No processing required
        </div>
      )}
    </div>
  );
});

function App() {
  const [projects, setProjects] = useState([]);
  const [currentProject, setCurrentProject] = useState(null);
  const [jobData, setJobData] = useState(null);
  const [status, setStatus] = useState({ is_processing: false, queue: [], last_completed: null });
  const [toast, setToast] = useState(null);
  
  const [token, setToken] = useState(localStorage.getItem("dfs_token") || "");
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  const [char1, setChar1] = useState(null);
  const [char2, setChar2] = useState(null);
  const [hasCustomChars, setHasCustomChars] = useState({ char1: false, char2: false });

  const [activeClip, setActiveClip] = useState(null);
  const [frameUrl, setFrameUrl] = useState(null);
  const [maskPoints, setMaskPoints] = useState({ positive: [], negative: [] });
  const [frameIndex, setFrameIndex] = useState(0);
  const [modalTab, setModalTab] = useState(1);
  const lastAlertedJob = useRef(null);

  useEffect(() => {
    const checkAuth = async () => {
      try {
        if(token) {
           axios.defaults.headers.common['X-Access-Token'] = token;
           await axios.get(`${API_BASE}/`); 
           setIsAuthenticated(true);
        }
      } catch(e) {
        setIsAuthenticated(false);
      }
    };
    checkAuth();
  }, [token]);

  useEffect(() => {
    if(!isAuthenticated) return;
    fetchProjects();
    checkCustomChars();
    const interval = setInterval(() => { fetchStatusAndProject(); }, 1000);
    return () => clearInterval(interval);
  }, [currentProject, isAuthenticated]);

  const fetchStatusAndProject = async () => {
    const timestamp = Date.now();
    try {
      const res = await axios.get(`${API_BASE}/status`, { params: { t: timestamp } });
      const newStatus = res.data;
      setStatus(prev => {
        if (JSON.stringify(prev) !== JSON.stringify(newStatus)) return newStatus;
        return prev;
      });

      if (newStatus.last_completed && newStatus.last_completed !== lastAlertedJob.current) {
        setToast(`Job Completed: ${newStatus.last_completed}`);
        lastAlertedJob.current = newStatus.last_completed;
        if (currentProject) fetchProjectData(currentProject);
      }
    } catch(e) {}
    if (currentProject) fetchProjectData(currentProject);
  };

  const fetchProjectData = async (projectId) => {
    try {
      const res = await axios.get(`${API_BASE}/project/${projectId}`, { params: { t: Date.now() } });
      setJobData(prev => {
        if (JSON.stringify(prev) !== JSON.stringify(res.data)) return res.data;
        return prev;
      });
    } catch(e) {}
  };

  const fetchProjects = async () => {
    const res = await axios.get(`${API_BASE}/projects`);
    setProjects(res.data);
  };

  const checkCustomChars = async () => {
    const res = await axios.get(`${API_BASE}/characters/check`, { params: { t: Date.now() } });
    setHasCustomChars(res.data);
    if(res.data.char1) setChar1(`${API_BASE}/assets/custom_char1.png?t=${Date.now()}`);
    if(res.data.char2) setChar2(`${API_BASE}/assets/custom_char2.png?t=${Date.now()}`);
  };

  const loadProject = async (id) => {
    if (!hasCustomChars.char1 || !hasCustomChars.char2) {
      if (!window.confirm("Warning: Custom characters not found. Continue?")) return;
    }
    setCurrentProject(id);
    setJobData(null);
    fetchProjectData(id);
  };

  const handleLogin = (e) => {
    if (e.key === 'Enter') {
      const val = e.target.value;
      localStorage.setItem("dfs_token", val);
      setToken(val);
    }
  };

  const handleUploadChar = async (name, file) => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await axios.post(`${API_BASE}/character/upload/${name}`, formData);
    const newUrl = `${API_BASE}${res.data.url}?t=${Date.now()}`;
    if (name === "char1") setChar1(newUrl);
    else setChar2(newUrl);
    checkCustomChars();
  };

  const openMaskModal = (clip) => {
    setActiveClip(clip);
    setModalTab(1);
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
      const res = await axios.get(`${API_BASE}/mask/load/${currentProject}/${clip.clip_id}/${pass}`, { params: { t: Date.now() } });
      setMaskPoints(res.data);
    } catch (e) {
      setMaskPoints({ positive: [], negative: [] });
    }
  };

  const saveMaskCurrentPass = async () => {
    if (!activeClip) return;
    await axios.post(`${API_BASE}/mask/save/${currentProject}/${activeClip.clip_id}/${modalTab}`, maskPoints);
  };

  // FIX: Reset mask logic connected to backend
  const resetMaskCurrentPass = async () => {
    if (!activeClip) return;
    if (window.confirm("Are you sure you want to reset the saved mask for this pass?")) {
      await axios.post(`${API_BASE}/mask/reset/${currentProject}/${activeClip.clip_id}/${modalTab}`);
      setMaskPoints({ positive: [], negative: [] }); // Clear UI immediately
      setToast("Mask reset successfully.");
    }
  };

  // FIX: Force synchronous clearing of mask UI when switching tabs to prevent state bleeding
  const handleTabChange = async (newPass) => {
    await saveMaskCurrentPass();
    setMaskPoints({ positive: [], negative: [] }); // Instantly wipes screen before new load happens
    setModalTab(newPass);
    await loadMaskForPass(activeClip, newPass);
  };

  const confirmAndGenerateSingle = async () => {
    await saveMaskCurrentPass();
    await axios.post(`${API_BASE}/queue/clip/${currentProject}`, activeClip);
    setActiveClip(null);
    setToast(`Job Queued: ${activeClip.clip_id}`);
  };

  // FIX: Shows accurate alert when required masks aren't drawn yet
  const handleQueueAll = async () => {
    const res = await axios.post(`${API_BASE}/queue/all/${currentProject}`);
    if (res.data.status === "error") {
      alert("Missing masks for the following clips. Please generate them first:\n\n" + res.data.missing.join("\n"));
    } else {
      setToast(`Queued ${res.data.count} clips.`);
    }
  };

  const handleStop = async () => {
    await axios.post(`${API_BASE}/stop`);
    setToast("Stop signal sent.");
  };

  const handleReset = async () => {
    if (window.confirm("Delete all generated clips?")) {
      await axios.post(`${API_BASE}/reset/${currentProject}`);
      setToast("Project reset.");
    }
  };

  const handleStitch = async () => {
    setToast("Stitching video...");
    const res = await axios.post(`${API_BASE}/stitch/${currentProject}`);
    const link = document.createElement('a');
    link.href = `${API_BASE}${res.data.url}`;
    link.download = `${currentProject}_final.mp4`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setToast("Download starting...");
  };

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center font-sans text-white">
        <div className="bg-gray-800 p-8 rounded-xl shadow-lg border border-gray-700 w-96">
          <h2 className="text-2xl font-bold mb-4 text-blue-400">Access Restricted</h2>
          <p className="text-gray-400 text-sm mb-4">Please enter your access key to continue.</p>
          <input 
            type="password" 
            placeholder="Access Key"
            className="w-full p-3 rounded bg-gray-700 border border-gray-600 focus:border-blue-500 outline-none transition"
            onKeyDown={handleLogin}
          />
        </div>
      </div>
    );
  }

  if (!currentProject) {
    return (
      <div className="min-h-screen bg-gray-900 text-white p-10 font-sans">
        <h1 className="text-4xl font-bold mb-8 text-blue-400 text-center">DeepFake Studio</h1>
        <div className="max-w-4xl mx-auto bg-gray-800 p-8 rounded-xl mb-12 shadow-lg border border-gray-700">
          <h2 className="text-2xl font-semibold mb-6 flex items-center gap-2">
            <Layers className="text-blue-400"/> Character Setup
          </h2>
          <div className="flex gap-10 justify-center">
            {['char1', 'char2'].map(char => (
              <div key={char} className="flex flex-col items-center">
                <div className="w-40 h-40 bg-gray-700 rounded-lg mb-4 overflow-hidden border-2 border-dashed border-gray-500 flex items-center justify-center relative group hover:border-blue-500 transition-colors">
                  {char === 'char1' ? 
                    (char1 ? <img src={char1} className="w-full h-full object-cover" /> : <span className="text-gray-400">No Image</span>) : 
                    (char2 ? <img src={char2} className="w-full h-full object-cover" /> : <span className="text-gray-400">No Image</span>)
                  }
                </div>
                <label className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded cursor-pointer transition shadow-lg shadow-blue-900/20">
                  Upload {char === 'char1' ? 'Char 1' : 'Char 2'}
                  <input type="file" className="hidden" accept="image/*" onChange={(e) => handleUploadChar(char, e.target.files[0])} />
                </label>
              </div>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-4xl mx-auto">
          {projects.map(p => (
            <button key={p} onClick={() => loadProject(p)} className="bg-gray-800 hover:bg-gray-750 border border-gray-700 hover:border-blue-500 p-8 rounded-xl transition shadow-md flex flex-col items-center gap-4 group">
              <FileVideo className="w-12 h-12 text-blue-500 group-hover:scale-110 transition" />
              <span className="text-lg font-medium group-hover:text-blue-300">{p}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6 pb-24 font-sans relative">
      {toast && <Toast message={toast} onClose={() => setToast(null)} />}
      
      <div className="flex justify-between items-center mb-8 bg-gray-800 p-4 rounded-lg shadow-md border border-gray-700">
        <div className="flex items-center gap-4">
          <button onClick={() => setCurrentProject(null)} className="flex items-center gap-2 text-gray-400 hover:text-white transition bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded">
            <ArrowLeft size={18} /> Back
          </button>
          <h1 className="text-xl font-bold text-blue-400 border-l border-gray-600 pl-4">{currentProject}</h1>
        </div>
        <div className="flex gap-3">
          <button onClick={handleReset} className="bg-red-900/30 hover:bg-red-900/50 text-red-300 px-4 py-2 rounded flex items-center gap-2 border border-red-900/50 transition">
            <Trash2 size={18} /> Reset Project
          </button>
          {status.is_processing ? (
            <button onClick={handleStop} className="bg-red-600 hover:bg-red-500 px-6 py-2 rounded flex items-center gap-2 animate-pulse font-bold shadow-lg shadow-red-900/30">
              <StopCircle size={18} /> Stop Generation ({status.queue_size} queued)
            </button>
          ) : (
            <button onClick={handleQueueAll} className="bg-blue-600 hover:bg-blue-500 px-6 py-2 rounded flex items-center gap-2 font-bold shadow-lg shadow-blue-900/20 transition">
              <Play size={18} /> Queue Entire Video
            </button>
          )}
          <button onClick={handleStitch} className="bg-emerald-600 hover:bg-emerald-500 px-6 py-2 rounded flex items-center gap-2 font-bold shadow-lg shadow-emerald-900/20 transition">
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
          onReset={resetMaskCurrentPass} // Passed new prop
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

const MaskModal = ({ 
  clip, frameUrl, setFrameUrl, frameIndex, setFrameIndex,
  maskPoints, setMaskPoints, modalTab, onTabChange,
  onSave, onReset, onConfirm, onClose, loadFrame, loadLastMask, currentProject 
}) => {
  const actions = clip.actions || [];
  const isMultiPass = actions.length > 1;
  const currentAction = actions.find(a => a.pass === modalTab);
  const resolution = (currentProject === "Video2") ? { width: 480, height: 832 } : { width: 832, height: 480 };

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 backdrop-blur-md">
      <div className="bg-gray-800 p-6 rounded-xl w-[1000px] max-h-[95vh] overflow-y-auto relative shadow-2xl border border-gray-700">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-2xl font-bold text-white flex items-center gap-2">
            <Layers className="text-blue-400"/> Masking: <span className="text-gray-400 text-lg font-normal">{clip.clip_id}</span>
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white hover:bg-gray-700 p-2 rounded-full transition">
            <X size={24}/>
          </button>
        </div>
        
        {isMultiPass && (
          <div className="flex gap-2 mb-4 border-b border-gray-700 pb-1">
            {actions.map(action => (
              <button 
                key={action.pass} 
                onClick={() => onTabChange(action.pass)} 
                className={`px-6 py-2 rounded-t-lg transition font-medium ${
                  modalTab === action.pass 
                    ? "bg-blue-600 text-white" 
                    : "bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200"
                }`}
              >
                Pass {action.pass}: {action.character}
              </button>
            ))}
          </div>
        )}

        <div className="bg-gray-700/30 border border-gray-700 p-4 rounded-lg mb-4 text-sm text-gray-300 flex justify-between items-center">
          <span className="flex items-center gap-2">
            Target Character: <strong className="text-blue-300 text-lg">{currentAction?.character || "N/A"}</strong>
          </span>
          <span className="flex gap-6">
            <span className="flex items-center gap-2"><div className="w-3 h-3 rounded-full bg-lime-500 shadow-[0_0_8px_rgba(132,204,22,0.6)]"></div> Left Click (Positive)</span>
            <span className="flex items-center gap-2"><div className="w-3 h-3 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]"></div> Shift + Click (Negative)</span>
          </span>
        </div>

        <div className="flex gap-3 mb-4">
          <button onClick={() => { setFrameIndex(0); loadFrame(clip, 0); }} className="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-sm transition">Reset Frame</button>
          <button onClick={() => { const f = prompt("Frame Number:"); if(f) { setFrameIndex(parseInt(f)); loadFrame(clip, parseInt(f)); }}} className="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-sm transition">Jump to Frame</button>
          <div className="flex-grow"></div>
          {/* FIX: Added Reset button next to Save / Reload */}
          <button onClick={onReset} className="bg-red-600/20 hover:bg-red-600/40 text-red-200 border border-red-600/50 px-4 py-1 rounded text-sm transition flex items-center gap-2"><Trash2 size={14}/> Reset Mask</button>
          <button onClick={loadLastMask} className="bg-yellow-600/20 hover:bg-yellow-600/40 text-yellow-200 border border-yellow-600/50 px-4 py-1 rounded text-sm transition flex items-center gap-2"><ArrowLeft size={14}/> Reload Saved</button>
          <button onClick={onSave} className="bg-blue-600/20 hover:bg-blue-600/40 text-blue-200 border border-blue-600/50 px-4 py-1 rounded text-sm transition flex items-center gap-2"><Download size={14}/> Save Progress</button>
        </div>

        <div className="bg-black/60 rounded-lg flex justify-center p-4 min-h-[400px] border border-gray-700">
          {frameUrl ? (
            <MaskCanvas imageUrl={frameUrl} resolution={resolution} points={maskPoints} onChange={setMaskPoints} />
          ) : (
            <div className="flex flex-col items-center justify-center text-gray-500 h-[400px]">
              <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mb-2"></div>
              Loading frame...
            </div>
          )}
        </div>

        <div className="flex justify-end gap-4 mt-6">
          <button onClick={onClose} className="text-gray-400 hover:text-white px-6 py-2 transition">Cancel</button>
          <button onClick={onConfirm} className="bg-green-600 hover:bg-green-500 px-8 py-2 rounded-lg font-bold shadow-lg shadow-green-900/30 flex items-center gap-2 transition transform hover:scale-105">
            <CheckCircle size={18}/> Confirm & Queue
          </button>
        </div>
      </div>
    </div>
  );
};

function MaskCanvas({ imageUrl, resolution, points, onChange }) {
  const canvasRef = useRef(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    ctx.fillStyle = "#84cc16"; 
    ctx.shadowColor = "rgba(132, 204, 22, 0.5)";
    ctx.shadowBlur = 10;
    points.positive.forEach(p => { ctx.beginPath(); ctx.arc(p.x, p.y, 6, 0, 2 * Math.PI); ctx.fill(); });
    
    ctx.fillStyle = "#ef4444"; 
    ctx.shadowColor = "rgba(239, 68, 68, 0.5)";
    ctx.shadowBlur = 10;
    points.negative.forEach(p => { ctx.beginPath(); ctx.arc(p.x, p.y, 6, 0, 2 * Math.PI); ctx.fill(); });
    
    ctx.shadowBlur = 0;
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
    <div style={{ position: "relative", width: resolution.width, height: resolution.height, boxShadow: "0 0 20px rgba(0,0,0,0.5)" }}>
      <img src={imageUrl} alt="frame" style={{ width: resolution.width, height: resolution.height }} className="select-none pointer-events-none" />
      <canvas ref={canvasRef} width={resolution.width} height={resolution.height} onClick={handleClick} onContextMenu={(e) => e.preventDefault()} style={{ position: "absolute", top: 0, left: 0, cursor: "crosshair" }} />
    </div>
  );
}

export default App;