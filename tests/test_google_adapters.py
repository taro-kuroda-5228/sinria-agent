from agent.company_context.google_adapters import GoogleDriveChangesAdapter, GoogleGmailApprovalAdapter, GoogleGmailMetadataAdapter
import hashlib

class Req:
    def __init__(self, value): self.value = value
    def execute(self): return self.value
class Changes:
    def __init__(self): self.calls=[]
    def getStartPageToken(self, **kw): self.calls.append(("start",kw)); return Req({"startPageToken":"s0"})
    def list(self, **kw):
        self.calls.append(("list",kw)); return Req({"changes":[{"fileId":"a"}],"newStartPageToken":"s1"})
class Files:
    def export_media(self, **kw): self.call=kw; return Req(b"hello")
class Drive:
    def __init__(self): self.c, self.f=Changes(), Files()
    def changes(self): return self.c
    def files(self): return self.f

def test_drive_uses_shared_drive_shape_and_export():
    service=Drive(); seen=[]
    assert GoogleDriveChangesAdapter(service, drive_id="shared").sync(seen.append)==1
    assert service.c.calls[0] == ("start", {"supportsAllDrives": True, "driveId": "shared"})
    assert service.c.calls[1][1]["includeItemsFromAllDrives"] is True
    assert GoogleDriveChangesAdapter(service).export("a")==b"hello"
    assert service.f.call == {"fileId":"a", "mimeType":"text/plain"}

class Messages:
    def list(self, **kw): self.list_call=kw; return Req({"messages":[{"id":"m1"}]})
    def get(self, **kw):
        self.get_call=kw
        return Req({"id":kw["id"],"threadId":"t1","payload":{"headers":[{"name":"X-Sinria-Private-Signal","value":"yes"}]}})
    def send(self, **kw): self.send_call=kw; return Req({"id":"sent"})
class Users:
    def __init__(self): self.m=Messages()
    def messages(self): return self.m
class Gmail:
    def __init__(self): self.u=Users()
    def users(self): return self.u

def test_gmail_metadata_private_signal_and_approval_readback():
    service=Gmail(); rows=GoogleGmailMetadataAdapter(service).list_metadata(private_only=True)
    assert rows[0]["private_signal"] == "yes"
    raw="From: a\n\nbody"; approval={"owner_id":"o","payload_hash":hashlib.sha256(raw.encode()).hexdigest(),"expires_at":20}
    result=GoogleGmailApprovalAdapter(service, owner_id="o", clock=lambda:10).send(raw_message=raw, approval=approval)
    assert result["id"] == "sent" and service.u.m.send_call["userId"] == "me"
