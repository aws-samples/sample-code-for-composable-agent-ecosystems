"use client";

import { LineChart, Line, BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

interface A2UIChartProps {
  title?: string;
  chart_type?: "line" | "bar" | "pie" | "sankey";
  data?: { labels?: string[]; values?: number[]; series?: { name: string; values: number[] }[] };
  [key: string]: unknown;
}

const COLORS = ["#ef4444", "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6"];

export function A2UIChart({ title, chart_type = "line", data }: A2UIChartProps) {
  if (!data) return null;

  const chartData = data.labels?.map((label, i) => ({
    name: label,
    value: data.values?.[i] ?? 0,
  })) ?? [];

  return (
    <div className="bg-battlefield-card border border-battlefield-border rounded-lg p-4">
      {title && <h3 className="font-semibold text-white mb-3">{title}</h3>}
      <ResponsiveContainer width="100%" height={200}>
        {chart_type === "line" ? (
          data.series ? (
            <LineChart data={data.series[0]?.values.map((_, i) => {
              const point: Record<string, number> = { idx: i };
              data.series!.forEach((s) => { point[s.name] = s.values[i] ?? 0; });
              return point;
            })}>
              <XAxis dataKey="idx" stroke="#64748b" />
              <YAxis stroke="#64748b" />
              <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
              {data.series.map((s, i) => (
                <Line key={s.name} type="monotone" dataKey={s.name} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={false} />
              ))}
            </LineChart>
          ) : (
            <LineChart data={chartData}>
              <XAxis dataKey="name" stroke="#64748b" />
              <YAxis stroke="#64748b" />
              <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
              <Line type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2} />
            </LineChart>
          )
        ) : chart_type === "bar" ? (
          <BarChart data={chartData}>
            <XAxis dataKey="name" stroke="#64748b" />
            <YAxis stroke="#64748b" />
            <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
            <Bar dataKey="value" fill="#3b82f6" />
          </BarChart>
        ) : (
          <PieChart>
            <Pie data={chartData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80}>
              {chartData.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
          </PieChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
