"use client";

import ReactMarkdown from "react-markdown";

interface A2UICardProps {
  title?: string;
  content?: string;
  sections?: { label: string; items?: { name: string; status: string; color?: string }[]; value?: string; trend?: string }[];
  [key: string]: unknown;
}

export function A2UICard({ title, content, sections }: A2UICardProps) {
  return (
    <div className="bg-battlefield-card border border-battlefield-border rounded-lg p-4">
      {title && <h3 className="font-semibold text-white mb-2">{title}</h3>}
      {content && <div className="text-gray-300 text-sm prose prose-invert prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0 prose-headings:my-2"><ReactMarkdown>{content}</ReactMarkdown></div>}
      {sections?.map((section, i) => (
        <div key={i} className="mt-3">
          <span className="text-xs text-gray-500 uppercase">{section.label}</span>
          {section.items && (
            <div className="mt-1 space-y-1">
              {section.items.map((item, j) => (
                <div key={j} className="flex items-center gap-2 text-sm">
                  <span className={`w-2 h-2 rounded-full bg-${item.color || "gray"}-500`} />
                  <span>{item.name}</span>
                  <span className="text-gray-500 ml-auto">{item.status}</span>
                </div>
              ))}
            </div>
          )}
          {section.value && (
            <div className="text-lg font-bold text-white">
              {section.value}
              {section.trend && <span className="text-xs text-gray-400 ml-2">({section.trend})</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
