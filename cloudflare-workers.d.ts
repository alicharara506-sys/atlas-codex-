interface Fetcher {
  fetch(input: Request): Promise<Response>;
}
