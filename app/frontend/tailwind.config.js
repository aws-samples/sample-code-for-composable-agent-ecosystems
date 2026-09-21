/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        tessera: {
          bg: "#0a0e1a",
          surface: "#111827",
          card: "#1a1f35",
          border: "#2a2f45",
          accent: "#8b5cf6",
          glow: "#6366f1",
        },
        red: { swarm: "#ef4444", glow: "#dc2626" },
        blue: { swarm: "#3b82f6", glow: "#2563eb" },
      },
      backgroundImage: {
        "tessera-gradient": "linear-gradient(135deg, #0ea5e9, #8b5cf6, #ec4899)",
        "card-gradient": "linear-gradient(180deg, rgba(139, 92, 246, 0.05) 0%, transparent 100%)",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
