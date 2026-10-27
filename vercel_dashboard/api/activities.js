const {
    supabaseRequest,
    sendJson,
    methodNotAllowed,
    handleDatabaseError
} = require("../lib/supabase");

module.exports = async function handler(req, res) {
    if (req.method !== "GET") return methodNotAllowed(res, "GET");
    try {
        const rows = await supabaseRequest(
            "activity_predictions?select=id,activity,confidence,source,timestamp&order=timestamp.desc,id.desc&limit=50",
            {}
        );
        return sendJson(res, 200, Array.isArray(rows) ? rows : []);
    } catch (error) {
        return handleDatabaseError(res, error);
    }
};
