import axios from "axios";

const API_BASE_URL =
  import.meta.env.VITE_API_URL ||
  import.meta.env.REACT_APP_API_URL ||
  "http://localhost:8000";

export async function sendChatMessage(payload) {
  const response = await axios.post(`${API_BASE_URL}/chat`, payload, {
    headers: { "Content-Type": "application/json" },
  });
  return response.data;
}
