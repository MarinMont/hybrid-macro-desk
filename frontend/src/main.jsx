import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import SetupConsole from "./SetupConsole.jsx";
import "./index.css";

// ハッシュで画面を切り替える (ルータ不要)。
//   #/setup → セットアップコンソール (handoff/SETUP_CONSOLE_SPEC.md)
//   それ以外 → ダッシュボード (従来どおり)
const Root = () => {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.startsWith("#/setup") ? <SetupConsole /> : <App />;
};

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
