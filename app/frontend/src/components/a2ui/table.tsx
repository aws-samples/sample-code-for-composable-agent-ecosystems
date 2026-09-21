"use client";

interface A2UITableProps {
  title?: string;
  columns?: string[];
  rows?: (string | number)[][];
  [key: string]: unknown;
}

export function A2UITable({ title, columns, rows }: A2UITableProps) {
  if (!columns || !rows) return null;

  return (
    <div className="bg-battlefield-card border border-battlefield-border rounded-lg p-4">
      {title && <h3 className="font-semibold text-white mb-3">{title}</h3>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-battlefield-border">
              {columns.map((col, i) => (
                <th key={i} className="text-left py-2 px-3 text-gray-400 font-medium">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-battlefield-border/50">
                {row.map((cell, j) => (
                  <td key={j} className="py-2 px-3 text-gray-300">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
