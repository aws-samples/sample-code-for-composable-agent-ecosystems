"use client";

/**
 * Animated Tessera mosaic spinner. Displayed during any wait state:
 * agent running, grounding in progress, optimizer analyzing, loading.
 * Replaces generic spinners with the platform's visual identity.
 */
export function TesseraSpinner({ size = 40, className = "" }: { size?: number; className?: string }) {
  return (
    <div className={`tessera-spinner ${className}`} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 500 500" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Loading">
        <defs>
          <linearGradient id="ts-g1" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#6c8cff" stopOpacity="0.9"/><stop offset="100%" stopColor="#4f6fdf" stopOpacity="0.7"/></linearGradient>
          <linearGradient id="ts-g2" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#fb923c" stopOpacity="0.9"/><stop offset="100%" stopColor="#ea580c" stopOpacity="0.7"/></linearGradient>
          <linearGradient id="ts-g3" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#ef4444" stopOpacity="0.9"/><stop offset="100%" stopColor="#dc2626" stopOpacity="0.7"/></linearGradient>
          <linearGradient id="ts-g4" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#fbbf24" stopOpacity="0.9"/><stop offset="100%" stopColor="#d97706" stopOpacity="0.7"/></linearGradient>
          <linearGradient id="ts-g5" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#22d3ee" stopOpacity="0.9"/><stop offset="100%" stopColor="#0891b2" stopOpacity="0.7"/></linearGradient>
        </defs>
        {/* Central star — rotates */}
        <g className="ts-star">
          <polygon fill="url(#ts-g5)" stroke="#22d3ee" strokeWidth="2" points="250,180 261.5,222.3 299.5,200.5 277.7,238.5 320,250 277.7,261.5 299.5,299.5 261.5,277.7 250,320 238.5,277.7 200.5,299.5 222.3,261.5 180,250 222.3,238.5 200.5,200.5 238.5,222.3"/>
        </g>
        {/* Tiles — pulse sequentially */}
        <polygon className="ts-tile ts-t1" fill="url(#ts-g1)" stroke="#6c8cff" strokeWidth="1" points="250,185 227.7,162.8 250,110 272.3,162.8"/>
        <polygon className="ts-tile ts-t2" fill="url(#ts-g4)" stroke="#fbbf24" strokeWidth="1" points="296,204 295.9,172.6 349,151 327.4,204.1"/>
        <polygon className="ts-tile ts-t3" fill="url(#ts-g1)" stroke="#6c8cff" strokeWidth="1" points="315,250 337.2,227.7 390,250 337.2,272.3"/>
        <polygon className="ts-tile ts-t4" fill="url(#ts-g2)" stroke="#fb923c" strokeWidth="1" points="296,296 327.4,295.9 349,349 295.9,327.4"/>
        <polygon className="ts-tile ts-t5" fill="url(#ts-g2)" stroke="#fb923c" strokeWidth="1" points="250,315 272.3,337.2 250,390 227.7,337.2"/>
        <polygon className="ts-tile ts-t6" fill="url(#ts-g4)" stroke="#fbbf24" strokeWidth="1" points="204,296 204.1,327.4 151,349 172.6,295.9"/>
        <polygon className="ts-tile ts-t7" fill="url(#ts-g3)" stroke="#ef4444" strokeWidth="1" points="185,250 162.8,272.3 110,250 162.8,227.7"/>
        <polygon className="ts-tile ts-t8" fill="url(#ts-g3)" stroke="#ef4444" strokeWidth="1" points="204,204 172.6,204.1 151,151 204.1,172.6"/>
        {/* Center jewel — glows */}
        <circle className="ts-jewel" cx="250" cy="250" r="8" fill="#22d3ee" opacity="0.8"/>
        <circle cx="250" cy="250" r="3.5" fill="#fff" opacity="0.95"/>
      </svg>
      <style jsx>{`
        .tessera-spinner {
          display: inline-flex;
          align-items: center;
          justify-content: center;
        }
        .ts-star {
          transform-origin: 250px 250px;
          animation: ts-rotate 3s linear infinite;
        }
        .ts-tile {
          opacity: 0.5;
          animation: ts-pulse 2s ease-in-out infinite;
        }
        .ts-t1 { animation-delay: 0s; }
        .ts-t2 { animation-delay: 0.25s; }
        .ts-t3 { animation-delay: 0.5s; }
        .ts-t4 { animation-delay: 0.75s; }
        .ts-t5 { animation-delay: 1s; }
        .ts-t6 { animation-delay: 1.25s; }
        .ts-t7 { animation-delay: 1.5s; }
        .ts-t8 { animation-delay: 1.75s; }
        .ts-jewel {
          animation: ts-glow 1.5s ease-in-out infinite alternate;
        }
        @keyframes ts-rotate {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes ts-pulse {
          0%, 100% { opacity: 0.4; transform: scale(1); }
          50% { opacity: 1; transform: scale(1.05); }
        }
        @keyframes ts-glow {
          from { filter: drop-shadow(0 0 4px rgba(34,211,238,0.4)); opacity: 0.7; }
          to { filter: drop-shadow(0 0 12px rgba(34,211,238,0.9)); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
