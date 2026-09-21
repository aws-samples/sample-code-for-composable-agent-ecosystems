"use client";

import { useState } from "react";

interface EcosystemInput {
  id: string;
  type: "file" | "text" | "select" | "number";
  label: string;
  accept?: string | string[];
  required?: boolean;
  options?: string[];
  default?: string;
  multiple?: boolean;
}

interface EcosystemInputFormProps {
  inputs: EcosystemInput[];
  onSubmit: (values: Record<string, string>) => void;
  loading?: boolean;
}

export function EcosystemInputForm({ inputs, onSubmit, loading }: EcosystemInputFormProps) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [fileNames, setFileNames] = useState<Record<string, string[]>>({});
  const [fileContents, setFileContents] = useState<Record<string, { name: string; content: string }[]>>({});

  if (!inputs || inputs.length === 0) return null;

  const handleFileChange = (id: string, files: FileList | null, multiple: boolean) => {
    if (!files || files.length === 0) return;
    const newFiles: Promise<{ name: string; content: string }>[] = [];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      newFiles.push(new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = (e) => resolve({ name: file.name, content: e.target?.result as string });
        reader.readAsText(file);
      }));
    }
    Promise.all(newFiles).then((parsed) => {
      setFileContents((prev) => {
        const existing = multiple ? (prev[id] || []) : [];
        const updated = [...existing, ...parsed];
        // Update display names
        setFileNames((f) => ({ ...f, [id]: updated.map((u) => u.name) }));
        // Combine into single value for submission
        const combined = updated.length === 1
          ? updated[0].content
          : updated.map((u) => `--- FILE: ${u.name} ---\n${u.content}`).join("\n\n");
        setValues((v) => ({ ...v, [id]: combined }));
        return { ...prev, [id]: updated };
      });
    });
  };

  const removeFile = (id: string, index: number) => {
    setFileContents((prev) => {
      const updated = [...(prev[id] || [])];
      updated.splice(index, 1);
      setFileNames((f) => ({ ...f, [id]: updated.map((u) => u.name) }));
      const combined = updated.length === 0 ? "" : updated.length === 1
        ? updated[0].content
        : updated.map((u) => `--- FILE: ${u.name} ---\n${u.content}`).join("\n\n");
      setValues((v) => ({ ...v, [id]: combined }));
      return { ...prev, [id]: updated };
    });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(values);
  };

  const acceptValue = (input: EcosystemInput) => {
    if (!input.accept) return undefined;
    return Array.isArray(input.accept) ? input.accept.join(",") : input.accept;
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4 p-4 bg-tessera-card rounded-lg border border-tessera-border">
      <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Ecosystem Inputs</h3>
      {inputs.map((input) => (
        <div key={input.id} className="space-y-1">
          <label className="block text-sm text-gray-300">
            {input.label} {input.required && <span className="text-red-400">*</span>}
          </label>
          {input.type === "file" && (
            <>
              <div className="flex items-center gap-2">
                <label className="cursor-pointer px-4 py-2 rounded text-sm bg-tessera-accent/20 text-tessera-accent hover:bg-tessera-accent/30 transition">
                  {input.multiple ? "Add files" : "Choose file"}
                  <input
                    type="file"
                    accept={acceptValue(input)}
                    multiple={!!input.multiple}
                    onChange={(e) => { handleFileChange(input.id, e.target.files, !!input.multiple); e.target.value = ""; }}
                    className="hidden"
                  />
                </label>
                {fileContents[input.id]?.length > 0 && (
                  <span className="text-[11px] text-gray-500">{fileContents[input.id].length} file{fileContents[input.id].length > 1 ? "s" : ""}</span>
                )}
              </div>
              {fileContents[input.id] && fileContents[input.id].length > 0 && (
                <div className="mt-1 space-y-1">
                  {fileContents[input.id].map((f, i) => (
                    <div key={i} className="flex items-center gap-2 text-[11px] text-gray-400 bg-gray-800/50 rounded px-2 py-1">
                      <span className="truncate flex-1">{f.name}</span>
                      <button type="button" onClick={() => removeFile(input.id, i)} className="text-red-400 hover:text-red-300 text-[9px]">✕</button>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
          {input.type === "text" && (
            <input
              type="text"
              value={values[input.id] || ""}
              onChange={(e) => setValues((v) => ({ ...v, [input.id]: e.target.value }))}
              className="w-full px-3 py-2 bg-tessera-bg border border-tessera-border rounded text-sm text-gray-200 focus:border-tessera-accent focus:outline-none"
              placeholder={input.label}
              required={input.required}
            />
          )}
          {input.type === "number" && (
            <input
              type="number"
              value={values[input.id] || input.default || ""}
              onChange={(e) => setValues((v) => ({ ...v, [input.id]: e.target.value }))}
              className="w-full px-3 py-2 bg-tessera-bg border border-tessera-border rounded text-sm text-gray-200 focus:border-tessera-accent focus:outline-none"
              required={input.required}
            />
          )}
          {input.type === "select" && (
            <select
              value={values[input.id] || input.default || ""}
              onChange={(e) => setValues((v) => ({ ...v, [input.id]: e.target.value }))}
              className="w-full px-3 py-2 bg-tessera-bg border border-tessera-border rounded text-sm text-gray-200 focus:border-tessera-accent focus:outline-none"
              required={input.required}
            >
              <option value="">Select...</option>
              {input.options?.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
            </select>
          )}
        </div>
      ))}
      <button
        type="submit"
        disabled={loading}
        className="w-full py-2 px-4 bg-tessera-accent text-white rounded text-sm font-medium hover:bg-tessera-accent/80 disabled:opacity-50"
      >
        {loading ? "Running..." : "Run Ecosystem"}
      </button>
    </form>
  );
}
