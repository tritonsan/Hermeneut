import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#2a2118",
        paper: "#fbf7ec",
        vellum: "#f2ead8",
        line: "#d8cab1",
        umber: "#5b3f26",
        copper: "#a85b2c",
        bronze: "#7a562f",
        sage: "#59685c",
        moss: "#7a866f",
        signal: "#256b5f",
        stone: "#ece7dc",
        night: "#24212a",
        mist: "#f7f8f3"
      },
      boxShadow: {
        soft: "0 14px 34px rgba(42, 33, 24, 0.08)",
        board: "0 22px 60px rgba(36, 33, 42, 0.12)",
        insetLine: "inset 0 1px 0 rgba(255,255,255,0.65)"
      }
    },
  },
  plugins: [],
};

export default config;
