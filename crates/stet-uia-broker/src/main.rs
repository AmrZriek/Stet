use std::io::{self, BufRead, Write};
use stet_uia_broker::handle_broker_request;
use stet_uia_broker::protocol::BrokerRequest;

fn main() {
    let stdin = io::stdin();
    let mut lines = stdin.lock().lines();
    if let Some(Ok(line)) = lines.next() {
        if let Ok(req) = serde_json::from_str::<BrokerRequest>(&line) {
            let resp = handle_broker_request(&req);
            if let Ok(serialized) = serde_json::to_string(&resp) {
                let _ = writeln!(io::stdout(), "{}", serialized);
                let _ = io::stdout().flush();
                return;
            }
        }
    }
}
