import { KeyRound } from "lucide-react";
import { useState } from "react";
import { getStoredApiKey, storeApiKey } from "../api";

export function ApiKeyField() {
  const [value, setValue] = useState(getStoredApiKey());
  const [saved, setSaved] = useState(Boolean(value));

  return (
    <div className="key-field">
      <KeyRound size={16} aria-hidden="true" />
      <label htmlFor="api-key">本机写入密钥</label>
      <input
        id="api-key"
        type="password"
        autoComplete="off"
        value={value}
        placeholder="输入本机访问密钥"
        onChange={(event) => { setValue(event.target.value); setSaved(false); }}
      />
      <button className="text-button" type="button" onClick={() => { storeApiKey(value.trim()); setSaved(Boolean(value.trim())); }}>
        {saved ? "已保存到本次会话" : "保存"}
      </button>
    </div>
  );
}
