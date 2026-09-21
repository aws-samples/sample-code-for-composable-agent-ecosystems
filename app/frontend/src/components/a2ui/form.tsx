"use client";

import { useState } from "react";

interface A2UIFormProps {
  title?: string;
  fields?: { name: string; label: string; type: string; options?: string[] }[];
  [key: string]: unknown;
}

export function A2UIForm({ title, fields }: A2UIFormProps) {
  const [values, setValues] = useState<Record<string, string>>({});

  if (!fields) return null;

  return (
    <div className="bg-battlefield-card border border-battlefield-border rounded-lg p-4">
      {title && <h3 className="font-semibold text-white mb-3">{title}</h3>}
      <div className="space-y-3">
        {fields.map((field) => (
          <div key={field.name}>
            <label className="text-sm text-gray-400 block mb-1">{field.label}</label>
            {field.type === "select" && field.options ? (
              <select
                className="w-full bg-gray-800 border border-battlefield-border rounded px-3 py-2 text-sm text-white"
                value={values[field.name] || ""}
                onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
              >
                <option value="">Select...</option>
                {field.options.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            ) : (
              <input
                type={field.type || "text"}
                className="w-full bg-gray-800 border border-battlefield-border rounded px-3 py-2 text-sm text-white"
                value={values[field.name] || ""}
                onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
