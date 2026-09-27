const naturalDatePattern = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Accept only a real Gregorian calendar day; Date.parse normalizes 02-30. */
export function isJdNaturalDate(value: string) {
  const match = naturalDatePattern.exec(value);
  if (!match) return false;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function assertJdNaturalDateRange(startDate: string, endDate: string) {
  if (!isJdNaturalDate(startDate) || !isJdNaturalDate(endDate)) {
    throw new Error("京东日期范围必须使用真实存在的 YYYY-MM-DD 自然日。");
  }
  if (startDate > endDate) throw new Error("京东日期范围开始日不能晚于结束日。");
  return { startDate, endDate };
}

export function jdNaturalDatesInRange(startDate: string, endDate: string) {
  assertJdNaturalDateRange(startDate, endDate);
  const dates: string[] = [];
  for (let current = startDate; current <= endDate;) {
    dates.push(current);
    const date = new Date(`${current}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + 1);
    current = date.toISOString().slice(0, 10);
  }
  return dates;
}
