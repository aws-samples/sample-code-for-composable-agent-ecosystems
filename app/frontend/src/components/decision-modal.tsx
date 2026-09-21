"use client";

import { useState } from "react";
import { UserInputRequiredEvent } from "@/lib/ag-ui-types";

interface DecisionModalProps {
  decision: UserInputRequiredEvent;
  onSubmit: (decisionId: string, response: string) => void;
}

export function DecisionModal({ decision, onSubmit }: DecisionModalProps) {
  const [textInput, setTextInput] = useState("");

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-battlefield-card border border-yellow-500/50 rounded-xl p-6 max-w-lg w-full mx-4 shadow-2xl shadow-yellow-500/10">
        {/* Header */}
        <div className="flex items-center gap-2 mb-4">
          <span className="text-2xl">⚠️</span>
          <h2 className="text-lg font-bold text-yellow-400">Decision Required</h2>
        </div>

        {/* Question */}
        <p className="text-gray-200 mb-6 leading-relaxed">{decision.question}</p>

        {/* Options or Text Input */}
        {decision.input_type === "text" ? (
          <div className="space-y-4">
            <input
              type="text"
              className="w-full bg-gray-800 border border-battlefield-border rounded-lg px-4 py-3 text-white placeholder-gray-500"
              placeholder="Type your response..."
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && textInput.trim()) {
                  onSubmit(decision.decision_id, textInput.trim());
                }
              }}
              autoFocus
            />
            <button
              onClick={() => onSubmit(decision.decision_id, textInput.trim())}
              disabled={!textInput.trim()}
              className="w-full py-3 bg-yellow-600 hover:bg-yellow-700 disabled:bg-gray-700 rounded-lg font-medium transition"
            >
              Submit
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            {decision.options?.map((option) => (
              <button
                key={option}
                onClick={() => onSubmit(decision.decision_id, option)}
                className="w-full py-3 px-4 bg-gray-800 hover:bg-gray-700 border border-battlefield-border hover:border-yellow-500/50 rounded-lg text-left transition"
              >
                {option}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
