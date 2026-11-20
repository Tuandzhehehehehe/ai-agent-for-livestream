import React, { useState } from 'react';

export function AiChipStrip({ messages, selectedId, onSelect }) {
  return (
    <div
      id="livestream-ai-message-row"
      role="region"
      aria-label="AI selected live chat messages"
    >
      {messages.map((record) => {
        const isActive = selectedId === record.comment_id;
        return (
          <button
            key={record.comment_id}
            type="button"
            className={`livestream-ai-chip${isActive ? ' is-active' : ''}`}
            data-comment-id={record.comment_id}
            aria-expanded={String(isActive)}
            title={`${record.sender}: ${record.text}`}
            onClick={() => onSelect(record.comment_id)}
          >
            <span className="livestream-ai-chip-author">{record.sender}</span>
            <span className="livestream-ai-chip-text">{record.text}</span>
          </button>
        );
      })}
    </div>
  );
}

export function AiCommentPopover({ record, onClose, onSubmitReply }) {
  const [noteText, setNoteText] = useState('');

  if (!record) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!noteText.trim()) return;
    onSubmitReply(record, noteText.trim());
    setNoteText('');
  };

  return (
    <div id="livestream-ai-detail-popover" role="dialog">
      <div className="livestream-ai-popover-header">
        <div className="livestream-ai-popover-meta">
          <div className="livestream-ai-popover-author">{record.sender}</div>
          <div className="livestream-ai-popover-badges">
            <span className="livestream-ai-popover-badge">
              {String(record.intent || 'selected').replace(/_/g, ' ')}
            </span>
            {record.auto_order?.order_id && (
              <span className="livestream-ai-popover-badge is-answered">
                Order #{record.auto_order.order_id}
              </span>
            )}
            {record.needs_human_review ? (
              <span className="livestream-ai-popover-badge is-review">
                Needs Review
              </span>
            ) : (
              record.answer_draft?.status === 'answered' && (
                <span className="livestream-ai-popover-badge is-answered">
                  Draft Ready
                </span>
              )
            )}
          </div>
        </div>
        <button
          type="button"
          className="livestream-ai-popover-close"
          onClick={onClose}
        >
          ×
        </button>
      </div>

      <div className="livestream-ai-thread-list">
        <div className="livestream-ai-popover-message">
          <div>{record.text}</div>
        </div>
        {(record.thread_messages || []).map((m, idx) => (
          <div key={idx} className="livestream-ai-popover-message is-reply">
            <div className="livestream-ai-popover-label">{m.sender}</div>
            <div>{m.text}</div>
          </div>
        ))}
      </div>

      <form className="livestream-ai-thread-composer" onSubmit={handleSubmit}>
        <input
          type="text"
          className="livestream-ai-thread-input"
          placeholder={`Internal draft / operator note for ${record.sender}...`}
          value={noteText}
          onChange={(e) => setNoteText(e.target.value)}
        />
        <button type="submit" className="livestream-ai-thread-send">
          Save Note
        </button>
      </form>
    </div>
  );
}
