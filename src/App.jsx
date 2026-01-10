import React, { useState, useEffect, useMemo, useRef } from 'react';
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer, 
  ReferenceLine,
  Area,
  ComposedChart
} from 'recharts';
import { 
  Plus, 
  Trash2, 
  TrendingUp, 
  DollarSign, 
  Calendar, 
  Settings, 
  Activity,
  Briefcase,
  Coffee,
  AlertCircle,
  Copy,
  Save,
  Check,
  X,
  Download,
  Upload
} from 'lucide-react';

// --- Utility Functions ---

const formatCurrency = (value) => {
  if (isNaN(value)) return '$0';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);
};

// --- Initial Data / Default State ---

const defaultData = {
  currentAge: 32,
  retirementAgeGoal: 50,
  currentNetWorth: 150000,
  annualIncome: 120000,
  annualSpending: 60000,
  inflation: 3,
  growthRate: 7, 
  phases: [
    { id: 1, name: 'Daycare / Private School', startAge: 32, endAge: 37, amount: 24000 },
    { id: 2, name: 'Mortgage Payments', startAge: 32, endAge: 60, amount: 0 }, 
  ],
  events: [
    { id: 1, name: 'Home Renovation', age: 35, amount: 40000 },
    { id: 2, name: 'New Car', age: 42, amount: 35000 },
  ],
  lifestyleToggles: [
    { id: 1, name: 'International Travel', amount: 15000, active: false },
    { id: 2, name: 'Country Club', amount: 8000, active: false },
  ]
};

const STORAGE_KEY_V1 = 'fire_calc_data_v1';
const STORAGE_KEY_V2 = 'fire_calc_data_v2';

// --- Components ---

const Modal = ({ isOpen, onClose, title, children }) => {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md overflow-hidden animate-in fade-in zoom-in duration-200">
        <div className="px-6 py-4 border-b border-slate-100 flex justify-between items-center bg-slate-50">
          <h3 className="font-semibold text-slate-800">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6">
          {children}
        </div>
      </div>
    </div>
  );
};

const Card = ({ children, title, icon: Icon, className = "" }) => (
  <div className={`bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden ${className}`}>
    {title && (
      <div className="bg-slate-50 px-4 py-3 border-b border-slate-100 flex items-center gap-2">
        {Icon && <Icon className="w-4 h-4 text-indigo-600" />}
        <h3 className="font-semibold text-slate-700 text-sm uppercase tracking-wide">{title}</h3>
      </div>
    )}
    <div className="p-5">
      {children}
    </div>
  </div>
);

const InputGroup = ({ label, value, onChange, type = "number", prefix = null, suffix = null, step = 1, helpText = null }) => (
  <div className="mb-4">
    <label className="block text-sm font-medium text-slate-700 mb-1">{label}</label>
    <div className="relative rounded-md shadow-sm">
      {prefix && (
        <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
          <span className="text-slate-500 sm:text-sm">{prefix}</span>
        </div>
      )}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        step={step}
        className={`block w-full rounded-md border-slate-300 py-2 focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm border px-3 ${prefix ? 'pl-8' : ''} ${suffix ? 'pr-8' : ''}`}
      />
      {suffix && (
        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3">
          <span className="text-slate-500 sm:text-sm">{suffix}</span>
        </div>
      )}
    </div>
    {helpText && <p className="mt-1 text-xs text-slate-500">{helpText}</p>}
  </div>
);

export default function App() {
  // --- State Management ---
  
  const [appState, setAppState] = useState(() => {
    // 1. Try loading V2 (Scenario Support)
    try {
      const savedV2 = localStorage.getItem(STORAGE_KEY_V2);
      if (savedV2) {
        return JSON.parse(savedV2);
      }
    } catch (e) {
      console.error("Failed to load V2 data", e);
    }

    // 2. Fallback: Try loading V1 and migrating
    try {
      const savedV1 = localStorage.getItem(STORAGE_KEY_V1);
      if (savedV1) {
        const v1Data = JSON.parse(savedV1);
        return {
          activeScenarioId: 'default',
          scenarios: [
            { id: 'default', name: 'Default Plan', ...v1Data }
          ]
        };
      }
    } catch (e) {
      console.error("Failed to migrate V1 data", e);
    }

    // 3. Default Initialization
    return {
      activeScenarioId: 'default',
      scenarios: [
        { id: 'default', name: 'Default Plan', ...defaultData }
      ]
    };
  });

  const [mounted, setMounted] = useState(false);
  
  // UI State for Modals
  const [modalConfig, setModalConfig] = useState({ type: null, isOpen: false });
  const [tempScenarioName, setTempScenarioName] = useState("");
  
  // Ref for file input
  const fileInputRef = useRef(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (mounted) {
      localStorage.setItem(STORAGE_KEY_V2, JSON.stringify(appState));
    }
  }, [appState, mounted]);

  // Helper to get current scenario data
  const data = appState.scenarios.find(s => s.id === appState.activeScenarioId) || appState.scenarios[0];

  // --- Actions ---

  // Update a field in the ACTIVE scenario
  const updateScenarioData = (updater) => {
    setAppState(prev => ({
      ...prev,
      scenarios: prev.scenarios.map(s => 
        s.id === prev.activeScenarioId ? updater(s) : s
      )
    }));
  };

  const updateField = (field, value) => {
    const numValue = value === "" ? 0 : Number(value);
    updateScenarioData(s => ({ ...s, [field]: numValue }));
  };

  // --- Phase Handlers ---
  const addPhase = () => {
    updateScenarioData(s => {
      const newId = Math.max(0, ...s.phases.map(p => p.id)) + 1;
      return {
        ...s,
        phases: [...s.phases, { id: newId, name: 'New Phase', startAge: s.currentAge, endAge: s.currentAge + 5, amount: 10000 }]
      };
    });
  };

  const removePhase = (id) => {
    updateScenarioData(s => ({ ...s, phases: s.phases.filter(p => p.id !== id) }));
  };

  const updatePhase = (id, field, value) => {
    updateScenarioData(s => ({
      ...s,
      phases: s.phases.map(p => p.id === id ? { ...p, [field]: field === 'name' ? value : Number(value) } : p)
    }));
  };

  // --- Event Handlers ---
  const addEvent = () => {
    updateScenarioData(s => {
      const newId = Math.max(0, ...s.events.map(e => e.id)) + 1;
      return {
        ...s,
        events: [...s.events, { id: newId, name: 'Big Expense', age: s.currentAge + 3, amount: 20000 }]
      };
    });
  };

  const removeEvent = (id) => {
    updateScenarioData(s => ({ ...s, events: s.events.filter(e => e.id !== id) }));
  };

  const updateEvent = (id, field, value) => {
    updateScenarioData(s => ({
      ...s,
      events: s.events.map(e => e.id === id ? { ...e, [field]: field === 'name' ? value : Number(value) } : e)
    }));
  };

  // --- Lifestyle Handlers ---
  const addLifestyle = () => {
    updateScenarioData(s => {
      const newId = Math.max(0, ...s.lifestyleToggles.map(l => l.id)) + 1;
      return {
        ...s,
        lifestyleToggles: [...s.lifestyleToggles, { id: newId, name: 'Luxury Item', amount: 5000, active: true }]
      };
    });
  };

  const removeLifestyle = (id) => {
    updateScenarioData(s => ({ ...s, lifestyleToggles: s.lifestyleToggles.filter(l => l.id !== id) }));
  };

  const toggleLifestyle = (id) => {
    updateScenarioData(s => ({
      ...s,
      lifestyleToggles: s.lifestyleToggles.map(l => l.id === id ? { ...l, active: !l.active } : l)
    }));
  };

  const updateLifestyle = (id, field, value) => {
    updateScenarioData(s => ({
      ...s,
      lifestyleToggles: s.lifestyleToggles.map(l => l.id === id ? { ...l, [field]: field === 'name' ? value : Number(value) } : l)
    }));
  };

  // --- Scenario Manager Handlers (Using Modals) ---
  
  const handleSwitchScenario = (e) => {
    setAppState(prev => ({ ...prev, activeScenarioId: e.target.value }));
  };

  // Open Modal Helpers
  const openAddScenarioModal = () => {
    setTempScenarioName("New Scenario");
    setModalConfig({ type: 'add', isOpen: true });
  };

  const openSaveAsModal = () => {
    setTempScenarioName(`Copy of ${data.name}`);
    setModalConfig({ type: 'saveAs', isOpen: true });
  };

  const openDeleteModal = () => {
    if (appState.scenarios.length <= 1) return;
    setModalConfig({ type: 'delete', isOpen: true });
  };

  const closeModal = () => {
    setModalConfig({ ...modalConfig, isOpen: false });
  };

  // Execution Handlers
  const executeAddScenario = () => {
    if (!tempScenarioName) return;
    const newId = Date.now().toString();
    setAppState(prev => {
      const baseData = prev.scenarios.find(s => s.id === 'default') || defaultData;
      const newScenario = { ...baseData, id: newId, name: tempScenarioName };
      return {
        ...prev,
        scenarios: [...prev.scenarios, newScenario],
        activeScenarioId: newId
      };
    });
    closeModal();
  };

  const executeSaveAs = () => {
    if (!tempScenarioName) return;
    const newId = Date.now().toString();
    setAppState(prev => {
      const currentData = prev.scenarios.find(s => s.id === prev.activeScenarioId);
      const newScenario = { ...currentData, id: newId, name: tempScenarioName };
      return {
        ...prev,
        scenarios: [...prev.scenarios, newScenario],
        activeScenarioId: newId
      };
    });
    closeModal();
  };

  const executeDelete = () => {
    setAppState(prev => {
      const remaining = prev.scenarios.filter(s => s.id !== prev.activeScenarioId);
      return {
        ...prev,
        scenarios: remaining,
        activeScenarioId: remaining[0].id
      };
    });
    closeModal();
  };

  // --- Export / Import Handlers ---

  const handleExportData = () => {
    const dataStr = JSON.stringify(appState, null, 2);
    const blob = new Blob([dataStr], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `FIRE_Calculator_Backup_${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleImportClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const importedData = JSON.parse(event.target.result);
        // Basic validation
        if (importedData.scenarios && importedData.activeScenarioId) {
          setAppState(importedData);
          alert("Data imported successfully!");
        } else {
          alert("Invalid file format. Please use a valid FIRE Calculator JSON export.");
        }
      } catch (err) {
        console.error(err);
        alert("Failed to parse file.");
      }
    };
    reader.readAsText(file);
    // Reset input
    e.target.value = null; 
  };


  // --- Calculation Engine ---

  const results = useMemo(() => {
    const simulationData = [];
    
    // Safety: ensure numbers are valid
    const currentAge = Number(data.currentAge) || 30;
    const maxAge = 90;
    
    let netWorth = Number(data.currentNetWorth) || 0;
    const growthRate = Number(data.growthRate) || 0;
    const inflation = Number(data.inflation) || 0;
    const annualIncomeRaw = Number(data.annualIncome) || 0;
    const annualSpendingRaw = Number(data.annualSpending) || 0;
    
    const safeWithdrawalRate = 0.04;
    let fireAge = null;

    for (let age = currentAge; age <= maxAge; age++) {
      const yearIndex = age - currentAge;
      
      const inflationFactor = Math.pow(1 + inflation / 100, yearIndex);
      
      const isWorking = age < data.retirementAgeGoal;
      const annualIncome = isWorking ? annualIncomeRaw * inflationFactor : 0;
      
      let currentExpenses = annualSpendingRaw * inflationFactor;

      const activePhases = data.phases.filter(p => age >= p.startAge && age <= p.endAge);
      const phaseCosts = activePhases.reduce((sum, p) => sum + (p.amount * inflationFactor), 0);

      const activeEvents = data.events.filter(e => e.age === age);
      const eventCosts = activeEvents.reduce((sum, e) => sum + (e.amount * inflationFactor), 0);

      const activeLifestyles = data.lifestyleToggles.filter(l => l.active);
      const lifestyleCosts = activeLifestyles.reduce((sum, l) => sum + (l.amount * inflationFactor), 0);

      const totalExpenses = currentExpenses + phaseCosts + eventCosts + lifestyleCosts;

      const growthAmount = netWorth * (growthRate / 100);
      const endOfYearNetWorth = netWorth + growthAmount + annualIncome - totalExpenses;

      const safeWithdrawalAmount = netWorth * safeWithdrawalRate;
      const isFI = safeWithdrawalAmount >= totalExpenses;

      if (isFI && fireAge === null) {
        fireAge = age;
      }

      simulationData.push({
        age,
        netWorth: Math.round(netWorth),
        totalExpenses: Math.round(totalExpenses),
        safeWithdrawalAmount: Math.round(safeWithdrawalAmount),
        passiveIncomeCrossover: isFI,
        isRetirementPhase: !isWorking
      });

      netWorth = endOfYearNetWorth;
    }

    return { simulationData, fireAge };
  }, [data]);

  const { simulationData, fireAge } = results;

  const minNetWorth = simulationData.length > 0 ? Math.min(...simulationData.map(d => d.netWorth)) : 0;
  const safeMin = minNetWorth < 0 ? minNetWorth : 0;

  // --- Render ---

  if (!mounted) return null;

  return (
    <div className="min-h-screen bg-slate-100 text-slate-800 font-sans pb-20">
      
      {/* Hidden File Input for Import */}
      <input 
        type="file" 
        ref={fileInputRef}
        className="hidden" 
        accept=".json"
        onChange={handleFileChange}
      />

      {/* --- MODALS --- */}
      
      {/* Add New Scenario Modal */}
      <Modal isOpen={modalConfig.type === 'add' && modalConfig.isOpen} onClose={closeModal} title="Create New Scenario">
        <p className="text-sm text-slate-600 mb-4">Create a new scenario starting with the defaults.</p>
        <input 
          className="w-full border border-slate-300 rounded-md p-2 text-sm mb-4 focus:ring-2 focus:ring-indigo-500 outline-none"
          placeholder="Scenario Name (e.g., Aggressive Saving)"
          value={tempScenarioName}
          onChange={(e) => setTempScenarioName(e.target.value)}
          autoFocus
        />
        <div className="flex justify-end gap-2">
          <button onClick={closeModal} className="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-md">Cancel</button>
          <button onClick={executeAddScenario} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700">Create</button>
        </div>
      </Modal>

      {/* Save As Modal */}
      <Modal isOpen={modalConfig.type === 'saveAs' && modalConfig.isOpen} onClose={closeModal} title="Duplicate Scenario">
        <p className="text-sm text-slate-600 mb-4">Create a copy of <strong>{data.name}</strong> to experiment with.</p>
        <input 
          className="w-full border border-slate-300 rounded-md p-2 text-sm mb-4 focus:ring-2 focus:ring-indigo-500 outline-none"
          value={tempScenarioName}
          onChange={(e) => setTempScenarioName(e.target.value)}
          autoFocus
        />
        <div className="flex justify-end gap-2">
          <button onClick={closeModal} className="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-md">Cancel</button>
          <button onClick={executeSaveAs} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700">Save Copy</button>
        </div>
      </Modal>

      {/* Delete Modal */}
      <Modal isOpen={modalConfig.type === 'delete' && modalConfig.isOpen} onClose={closeModal} title="Delete Scenario">
        <p className="text-sm text-slate-600 mb-6">
          Are you sure you want to delete <strong>{data.name}</strong>? This cannot be undone.
        </p>
        <div className="flex justify-end gap-2">
          <button onClick={closeModal} className="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-md">Cancel</button>
          <button onClick={executeDelete} className="px-4 py-2 text-sm bg-red-600 text-white rounded-md hover:bg-red-700">Delete</button>
        </div>
      </Modal>

      {/* --- HEADER --- */}
      <header className="bg-indigo-700 text-white shadow-lg sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-6 h-6 text-indigo-200" />
            <h1 className="text-xl font-bold tracking-tight hidden sm:block">Personal FIRE Calculator</h1>
            <h1 className="text-xl font-bold tracking-tight sm:hidden">FIRE Calc</h1>
          </div>

          <div className="flex items-center gap-4">
             {/* Data Actions */}
             <div className="flex items-center gap-1 border-r border-indigo-600 pr-4 mr-2">
               <button onClick={handleExportData} className="p-1.5 hover:bg-indigo-600 rounded text-indigo-200 hover:text-white" title="Export Data (Backup)">
                 <Download className="w-4 h-4" />
               </button>
               <button onClick={handleImportClick} className="p-1.5 hover:bg-indigo-600 rounded text-indigo-200 hover:text-white" title="Import Data">
                 <Upload className="w-4 h-4" />
               </button>
             </div>

             {/* Scenario Controls */}
             <div className="flex items-center gap-2 bg-indigo-800/50 p-1 rounded-lg">
                <div className="relative">
                  <select 
                    value={appState.activeScenarioId}
                    onChange={handleSwitchScenario}
                    className="appearance-none bg-indigo-900 text-white text-sm pl-3 pr-8 py-1.5 rounded-md border border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 cursor-pointer min-w-[140px]"
                  >
                    {appState.scenarios.map(s => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                  <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-indigo-300">
                    <Briefcase className="w-3 h-3" />
                  </div>
                </div>
                
                <button 
                  onClick={openAddScenarioModal}
                  title="Create New Scenario (from Default)"
                  className="p-1.5 hover:bg-indigo-600 rounded-md transition-colors text-indigo-200 hover:text-white"
                >
                  <Plus className="w-4 h-4" />
                </button>

                <button 
                  onClick={openSaveAsModal}
                  title="Save as new Scenario"
                  className="p-1.5 hover:bg-indigo-600 rounded-md transition-colors text-indigo-200 hover:text-white"
                >
                  <Copy className="w-4 h-4" />
                </button>
                
                <button 
                  onClick={openDeleteModal}
                  title="Delete current Scenario"
                  disabled={appState.scenarios.length <= 1}
                  className={`p-1.5 rounded-md transition-colors ${appState.scenarios.length <= 1 ? 'opacity-30 cursor-not-allowed' : 'hover:bg-red-500/20 hover:text-red-200 text-indigo-300'}`}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        
        {/* Scenario Banner Name */}
        <div className="mb-6 flex items-baseline gap-2">
           <span className="text-slate-500 text-sm font-medium uppercase tracking-wide">Editing Scenario:</span>
           <h2 className="text-2xl font-bold text-slate-800">{data.name}</h2>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          
          {/* LEFT COLUMN: INPUTS */}
          <div className="lg:col-span-4 space-y-6">
            
            {/* 1. Basic Inputs */}
            <Card title="The Basics" icon={Activity}>
              <div className="grid grid-cols-2 gap-4">
                <InputGroup 
                  label="Current Age" 
                  value={data.currentAge} 
                  onChange={(v) => updateField('currentAge', v)} 
                />
                <InputGroup 
                  label="Retire Goal Age" 
                  value={data.retirementAgeGoal} 
                  onChange={(v) => updateField('retirementAgeGoal', v)} 
                />
              </div>
              <InputGroup 
                label="Current Net Worth" 
                value={data.currentNetWorth} 
                onChange={(v) => updateField('currentNetWorth', v)} 
                prefix="$" 
                step={1000}
              />
              <InputGroup 
                label="Annual Income (Post-Tax)" 
                value={data.annualIncome} 
                onChange={(v) => updateField('annualIncome', v)} 
                prefix="$" 
                step={1000}
                helpText="Your take-home pay."
              />
              <InputGroup 
                label="Base Annual Spending" 
                value={data.annualSpending} 
                onChange={(v) => updateField('annualSpending', v)} 
                prefix="$" 
                step={1000}
                helpText="Survival budget excluding lumpy items below."
              />
            </Card>

            {/* 2. Phases (Lumpy Expenses) */}
            <Card title="Life Phases (The Lumpy Stuff)" icon={Calendar}>
              <div className="space-y-4">
                {data.phases.map((phase) => (
                  <div key={phase.id} className="bg-slate-50 p-3 rounded-md border border-slate-200 text-sm">
                    <div className="flex justify-between items-start mb-2">
                      <input 
                        className="font-medium bg-transparent border-b border-transparent focus:border-indigo-500 text-slate-700 w-full focus:outline-none"
                        value={phase.name}
                        onChange={(e) => updatePhase(phase.id, 'name', e.target.value)}
                      />
                      <button onClick={() => removePhase(phase.id)} className="text-slate-400 hover:text-red-500 ml-2">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                    <div className="grid grid-cols-3 gap-2 mb-2">
                      <div>
                        <label className="text-xs text-slate-500">Start Age</label>
                        <input 
                          type="number" 
                          className="w-full text-xs p-1 border rounded"
                          value={phase.startAge}
                          onChange={(e) => updatePhase(phase.id, 'startAge', e.target.value)}
                        />
                      </div>
                      <div>
                        <label className="text-xs text-slate-500">End Age</label>
                        <input 
                          type="number" 
                          className="w-full text-xs p-1 border rounded"
                          value={phase.endAge}
                          onChange={(e) => updatePhase(phase.id, 'endAge', e.target.value)}
                        />
                      </div>
                      <div>
                        <label className="text-xs text-slate-500">$/Year</label>
                        <input 
                          type="number" 
                          className="w-full text-xs p-1 border rounded"
                          value={phase.amount}
                          onChange={(e) => updatePhase(phase.id, 'amount', e.target.value)}
                        />
                      </div>
                    </div>
                  </div>
                ))}
                <button 
                  onClick={addPhase}
                  className="w-full py-2 flex items-center justify-center gap-2 text-sm text-indigo-600 font-medium border border-dashed border-indigo-300 rounded-md hover:bg-indigo-50 transition-colors"
                >
                  <Plus className="w-4 h-4" /> Add Phase (e.g. Daycare)
                </button>
              </div>
            </Card>

            {/* 3. One-Time Events */}
            <Card title="One-Time Events" icon={DollarSign}>
              <div className="space-y-3">
                {data.events.map((event) => (
                  <div key={event.id} className="flex items-center gap-2 text-sm">
                    <div className="flex-1 grid grid-cols-3 gap-2">
                      <input 
                        className="col-span-1 p-1 border rounded bg-slate-50 text-xs"
                        value={event.name}
                        onChange={(e) => updateEvent(event.id, 'name', e.target.value)}
                      />
                      <div className="flex items-center gap-1">
                        <span className="text-xs text-slate-500">Age:</span>
                        <input 
                          type="number"
                          className="w-full p-1 border rounded text-xs"
                          value={event.age}
                          onChange={(e) => updateEvent(event.id, 'age', e.target.value)}
                        />
                      </div>
                      <div className="relative">
                        <span className="absolute left-1 top-1 text-xs text-slate-500">$</span>
                        <input 
                          type="number"
                          className="w-full p-1 pl-3 border rounded text-xs"
                          value={event.amount}
                          onChange={(e) => updateEvent(event.id, 'amount', e.target.value)}
                        />
                      </div>
                    </div>
                    <button onClick={() => removeEvent(event.id)} className="text-slate-400 hover:text-red-500">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
                 <button 
                  onClick={addEvent}
                  className="w-full py-2 mt-2 flex items-center justify-center gap-2 text-sm text-indigo-600 font-medium border border-dashed border-indigo-300 rounded-md hover:bg-indigo-50 transition-colors"
                >
                  <Plus className="w-4 h-4" /> Add Event (e.g. Renovation)
                </button>
              </div>
            </Card>

            {/* 4. Fat FIRE Toggles */}
            <Card title="Fat FIRE Lifestyle Add-ons" icon={Coffee} className="border-indigo-100 ring-2 ring-indigo-50/50">
              <div className="space-y-3">
                 {data.lifestyleToggles.map((item) => (
                   <div key={item.id} className="flex items-center justify-between p-2 rounded-lg bg-slate-50 hover:bg-slate-100 transition-colors">
                     <div className="flex items-center gap-3 flex-1">
                        <div 
                          onClick={() => toggleLifestyle(item.id)}
                          className={`w-10 h-6 flex items-center rounded-full p-1 cursor-pointer transition-colors duration-300 ${item.active ? 'bg-indigo-600' : 'bg-slate-300'}`}
                        >
                          <div className={`bg-white w-4 h-4 rounded-full shadow-md transform duration-300 ${item.active ? 'translate-x-4' : 'translate-x-0'}`} />
                        </div>
                        <div className="flex flex-col">
                          <input 
                            value={item.name}
                            onChange={(e) => updateLifestyle(item.id, 'name', e.target.value)}
                            className="bg-transparent text-sm font-medium text-slate-700 focus:outline-none border-b border-transparent focus:border-indigo-300"
                          />
                          <div className="flex items-center text-xs text-slate-500">
                             +$
                             <input 
                                type="number"
                                value={item.amount}
                                onChange={(e) => updateLifestyle(item.id, 'amount', e.target.value)}
                                className="w-16 bg-transparent focus:outline-none border-b border-transparent focus:border-indigo-300 ml-1"
                              />
                             /yr
                          </div>
                        </div>
                     </div>
                     <button onClick={() => removeLifestyle(item.id)} className="text-slate-300 hover:text-red-400">
                        <Trash2 className="w-4 h-4" />
                      </button>
                   </div>
                 ))}
                 <button 
                  onClick={addLifestyle}
                  className="w-full py-1 text-xs text-indigo-600 hover:underline flex justify-center items-center gap-1"
                >
                  <Plus className="w-3 h-3" /> Add Luxury Item
                </button>
              </div>
            </Card>

             {/* 5. Assumptions */}
             <Card title="Scenario Assumptions" icon={Settings}>
                <div className="space-y-4">
                  <div>
                    <div className="flex justify-between mb-1">
                      <label className="text-sm font-medium text-slate-700">Annual Return</label>
                      <span className="text-sm font-bold text-indigo-600">{data.growthRate}%</span>
                    </div>
                    <input 
                      type="range" 
                      min="1" 
                      max="12" 
                      step="0.5" 
                      value={data.growthRate} 
                      onChange={(e) => updateField('growthRate', e.target.value)}
                      className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                    />
                    <div className="flex justify-between text-xs text-slate-400 mt-1">
                      <span>Bear (4%)</span>
                      <span>Avg (7%)</span>
                      <span>Bull (10%)</span>
                    </div>
                  </div>
                  
                  <div>
                    <div className="flex justify-between mb-1">
                      <label className="text-sm font-medium text-slate-700">Inflation</label>
                      <span className="text-sm font-bold text-indigo-600">{data.inflation}%</span>
                    </div>
                    <input 
                      type="range" 
                      min="1" 
                      max="8" 
                      step="0.5" 
                      value={data.inflation} 
                      onChange={(e) => updateField('inflation', e.target.value)}
                      className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                    />
                  </div>
                </div>
            </Card>

          </div>

          {/* RIGHT COLUMN: RESULTS */}
          <div className="lg:col-span-8 space-y-6">
            
            {/* Summary Banner */}
            <div className={`rounded-xl shadow-lg p-6 text-white flex flex-col md:flex-row items-center justify-between gap-6 transition-colors duration-500 ${fireAge ? 'bg-gradient-to-r from-emerald-600 to-teal-600' : 'bg-gradient-to-r from-slate-600 to-slate-700'}`}>
               <div>
                  <h2 className="text-lg font-medium opacity-90 mb-1">Financial Independence Estimate</h2>
                  {fireAge ? (
                    <div>
                      <span className="text-4xl font-bold">Age {fireAge}</span>
                      <span className="ml-3 text-emerald-100 font-medium">
                        ({fireAge - data.currentAge} years from now)
                      </span>
                    </div>
                  ) : (
                    <div className="text-2xl font-bold flex items-center gap-2">
                      <AlertCircle className="w-6 h-6" />
                      Not achieved by age 90
                    </div>
                  )}
               </div>
               <div className="bg-white/10 rounded-lg p-4 backdrop-blur-sm min-w-[200px]">
                 <div className="text-sm opacity-80">Projected NW at Goal ({data.retirementAgeGoal})</div>
                 <div className="text-2xl font-bold">
                    {formatCurrency(simulationData.find(d => d.age === data.retirementAgeGoal)?.netWorth || 0)}
                 </div>
               </div>
            </div>

            {/* Main Chart */}
            <Card className="h-[500px] flex flex-col">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-bold text-slate-800">Net Worth Trajectory</h3>
                <div className="flex items-center gap-4 text-sm">
                  <div className="flex items-center gap-1">
                    <div className="w-3 h-3 bg-indigo-500 rounded-full"></div>
                    <span>Net Worth</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <div className="w-3 h-3 bg-red-400 rounded-full opacity-50"></div>
                    <span>Expenses</span>
                  </div>
                </div>
              </div>

              <div className="flex-1 w-full min-h-0">
                {simulationData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={simulationData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="colorNw" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#6366f1" stopOpacity={0.2}/>
                          <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                      <XAxis 
                        dataKey="age" 
                        type="number"
                        domain={['dataMin', 'dataMax']}
                        tick={{fill: '#64748b', fontSize: 12}}
                        tickCount={10}
                      />
                      <YAxis 
                        tickFormatter={(value) => `$${value / 1000}k`}
                        tick={{fill: '#64748b', fontSize: 12}}
                        domain={[safeMin, 'auto']}
                      />
                      <Tooltip 
                        contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                        formatter={(value, name) => [formatCurrency(value), name === 'netWorth' ? 'Net Worth' : name === 'totalExpenses' ? 'Annual Expenses' : name]}
                        labelFormatter={(label) => `Age ${label}`}
                      />
                      
                      {/* Visual Crossover Marker */}
                      {fireAge && (
                        <ReferenceLine x={fireAge} stroke="#10b981" strokeDasharray="3 3">
                          <div className="text-xs text-emerald-600 font-bold bg-white px-1">FIRE</div>
                        </ReferenceLine>
                      )}
                      
                      <Area 
                        type="monotone" 
                        dataKey="netWorth" 
                        stroke="#6366f1" 
                        fillOpacity={1} 
                        fill="url(#colorNw)" 
                        strokeWidth={3}
                      />
                       
                       <Line 
                        type="monotone" 
                        dataKey={(data) => data.totalExpenses * 25} 
                        name="FIRE Target (25x Exp)" 
                        stroke="#ef4444" 
                        strokeDasharray="5 5" 
                        strokeWidth={2}
                        dot={false}
                      />

                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-full flex items-center justify-center text-slate-400">
                    <p>Enter a valid current age to see the projection.</p>
                  </div>
                )}
              </div>
              <p className="text-center text-xs text-slate-400 mt-2">
                Red dashed line represents the capital needed (25x Expenses) to retire. When Blue crosses Red, you are FI.
              </p>
            </Card>

            {/* Detailed Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
               <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                  <div className="text-xs text-slate-500 uppercase font-semibold mb-1">Fat/Lean Mode</div>
                  <div className="text-lg font-bold text-indigo-700">
                    {data.lifestyleToggles.some(l => l.active) ? "Fat FIRE" : "Lean FIRE"}
                  </div>
                  <div className="text-xs text-slate-400">
                    {data.lifestyleToggles.filter(l => l.active).length} active luxuries
                  </div>
               </div>
               
               <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                  <div className="text-xs text-slate-500 uppercase font-semibold mb-1">Expenses @ Goal</div>
                  <div className="text-lg font-bold text-slate-800">
                     {formatCurrency(simulationData.find(d => d.age === data.retirementAgeGoal)?.totalExpenses || 0)}
                  </div>
                  <div className="text-xs text-slate-400">adjusted for inflation</div>
               </div>

               <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                  <div className="text-xs text-slate-500 uppercase font-semibold mb-1">Safe Withdrawal</div>
                  <div className="text-lg font-bold text-emerald-600">
                     {formatCurrency((simulationData.find(d => d.age === data.retirementAgeGoal)?.netWorth || 0) * 0.04)}
                  </div>
                  <div className="text-xs text-slate-400">4% Rule / year</div>
               </div>

               <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                  <div className="text-xs text-slate-500 uppercase font-semibold mb-1">Total Events</div>
                  <div className="text-lg font-bold text-slate-800">
                     {data.events.length + data.phases.length}
                  </div>
                  <div className="text-xs text-slate-400">modeled inputs</div>
               </div>
            </div>

            {/* Explanation / Footer */}
            <div className="bg-slate-50 rounded-lg p-4 text-xs text-slate-500 border border-slate-200">
              <h4 className="font-bold text-slate-700 mb-1">How this calculation works:</h4>
              <p>
                This model iterates year-by-year from your current age to 90. Every year, it compounds your Net Worth by the growth rate, adds your income (inflated annually until retirement age), and subtracts expenses. Expenses are calculated by taking your Base Spending (inflated) and adding any active "Phases", "Events", or "Lifestyle Add-ons" scheduled for that specific age.
              </p>
              <p className="mt-2">
                <strong>The FIRE Crossover:</strong> We flag Financial Independence when your Safe Withdrawal Rate (4% of Net Worth) exceeds your total expenses for that specific year.
              </p>
            </div>

          </div>
        </div>
      </main>
    </div>
  );
}