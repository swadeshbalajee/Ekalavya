import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ListVideo, Plus } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "../lib/api";
import type { Playlist } from "../lib/types";
import { useToast } from "../lib/ui";
import { Modal, Spinner } from "./common";

export function PlaylistPicker({ videoId, onClose }: { videoId: string; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const [added, setAdded] = useState<Set<string>>(new Set());
  const playlists = useQuery({
    queryKey: ["library", "playlists"],
    queryFn: () => api<Playlist[]>("/v1/library/playlists"),
  });
  const add = useMutation({
    mutationFn: (playlist: Playlist) =>
      api<Playlist>(`/v1/library/playlists/${playlist.id}/items`, { body: { video_id: videoId } }),
    onSuccess: (playlist) => {
      setAdded((value) => new Set(value).add(playlist.id));
      toast(`Added to ${playlist.name}`);
      void client.invalidateQueries({ queryKey: ["library", "playlists"] });
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const create = useMutation({
    mutationFn: () => api<Playlist>("/v1/library/playlists", { body: { name: name.trim() } }),
    onSuccess: (playlist) => {
      setName("");
      add.mutate(playlist);
    },
    onError: (error) => toast(errorText(error), "error"),
  });

  return (
    <Modal open onClose={onClose} title="Add to playlist">
      {playlists.isLoading ? (
        <Spinner />
      ) : (
        <div className="space-y-1.5">
          {playlists.data?.map((playlist) => (
            <button
              key={playlist.id}
              onClick={() => add.mutate(playlist)}
              className="flex w-full items-center gap-3 rounded-xl border border-line px-3 py-2.5 text-left text-sm hover:border-primary/40"
            >
              <ListVideo size={16} className="text-muted" />
              <span className="flex-1">{playlist.name}</span>
              <span className="text-xs text-muted">{playlist.count} videos</span>
              {added.has(playlist.id) && <Check size={16} className="text-success" />}
            </button>
          ))}
          {playlists.data?.length === 0 && <p className="text-sm text-muted">No playlists yet.</p>}
        </div>
      )}
      <form
        className="mt-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <input
          className="input"
          placeholder="New playlist name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <button className="btn-primary shrink-0" disabled={!name.trim() || create.isPending}>
          <Plus size={16} /> Create
        </button>
      </form>
    </Modal>
  );
}
