/** A successful provider result must never be converted into a failed attempt
 * merely because the database acknowledgement or subsequent read was lost. */
export async function executeAnnotationAttempt<Prediction, Result, Failure>(input: {
  infer: () => Promise<Prediction>;
  complete: (prediction: Prediction) => Promise<Result>;
  fail: (error: unknown) => Promise<Failure>;
}): Promise<Result | Failure> {
  let prediction: Prediction;
  try { prediction = await input.infer(); }
  catch (error) { return input.fail(error); }
  return input.complete(prediction);
}
