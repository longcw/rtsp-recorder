/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      colors: {
        ink: {
          50: "#f6f7f9",
          100: "#eceef2",
          200: "#d4d8e0",
          300: "#aab1bf",
          400: "#7a8395",
          500: "#525c6f",
          600: "#3c4456",
          700: "#2a3142",
          800: "#1d2230",
          900: "#141823",
          950: "#0b0e16",
        },
        accent: {
          DEFAULT: "#7aa7ff",
          soft: "#1b2742",
        },
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.25), 0 0 0 1px rgba(255,255,255,0.04)",
      },
      animation: {
        "pulse-dot": "pulse-dot 1.6s ease-in-out infinite",
      },
      keyframes: {
        "pulse-dot": {
          "0%, 100%": { opacity: "0.55", transform: "scale(1)" },
          "50%": { opacity: "1", transform: "scale(1.25)" },
        },
      },
    },
  },
  plugins: [],
};
