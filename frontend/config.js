// Configurazione frontend — NESSUN dato sensibile qui (nessuna API key, nessun
// token: quelli restano solo sul backend, vedi backend/.env.example).
//
// Il backend serve questo stesso frontend (stesso dominio, stesso servizio),
// quindi di norma non serve toccare nulla: stringa vuota = stessa origine.
// Cambiala solo se decidi di ospitare il frontend altrove separatamente.
window.APP_CONFIG = {
  API_BASE_URL: "",
};
