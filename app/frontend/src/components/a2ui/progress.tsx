"use client";

interface A2UIProgressProps {
  title?: string;
  agent?: string;
  percent?: number;
  [key: string]: unknown;
}

export function A2UIProgress({ title, agent, percent = 0 }: A2UIProgressProps) {
  return (
    <div className="bg-battlefield-card border border-battlefield-border rounded-lg p-3">
      <div className="flex justify-between text-sm mb-1">
        <span className="text-gray-400">{title || agent || "Progress"}</span>
        <span className="text-white font-medium">{percent}%</span>
      </div>
      <div className="w-full bg-gray-700 rounded-full h-2">
        <div
          className="h-2 rounded-full transition-all duration-500 bg-blue-500"
          style={{ width: `${Math.min(100, percent)}%` }}
        />
      </div>
    </div>
  );
}
