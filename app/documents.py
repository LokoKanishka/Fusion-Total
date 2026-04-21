import math

def get_page_for_chunk(chunk_index: int, chunks_per_page: int = 5) -> int:
    """Calculates the logical page number (1-indexed) for a given chunk index."""
    if chunk_index < 0:
        return 1
    return (chunk_index // chunks_per_page) + 1

def get_total_pages(total_chunks: int, chunks_per_page: int = 5) -> int:
    """Calculates the total number of logical pages based on total chunks."""
    if total_chunks <= 0:
        return 1
    return math.ceil(total_chunks / chunks_per_page)

def get_chunks_for_page(all_chunks: list, page_number: int, chunks_per_page: int = 5) -> list:
    """Returns a slice of chunks for the given logical page."""
    if not all_chunks:
        return []
    start = (page_number - 1) * chunks_per_page
    end = start + chunks_per_page
    return all_chunks[start:end]
