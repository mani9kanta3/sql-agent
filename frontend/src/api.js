/*
  One axios instance for the whole page.

  There is no auth here and no interceptors, which is a real difference
  from the hardware store app. This API is read only and has no login, so
  there is no token to attach and nothing to refresh.

  The timeout is long on purpose. A question that needs three attempts
  makes three model calls, and the default of no timeout at all is worse
  than a long one, because a hung request just spins forever.
*/

import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL,
  timeout: 90000,
});

export default api;
